"""Versioned, exact-content JSON backups and preflighted restore plans."""

import io
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from .errors import BmError, UnsafePathError
from .io import parse_front_matter
from .paths import is_reserved_path
from .store import Store
from .urls import inspect_url

BACKUP_FORMAT = "bm-backup"
BACKUP_VERSION = 1


def _backup_record(store: Store, relative: str) -> dict[str, str]:
    """Read one exact-content record, refusing incomplete or unsafe backups."""
    path = store.exact_path(relative)
    if not path.is_file():
        raise BmError(f"bookmark is not a regular file: {relative!r}")
    try:
        content = path.read_bytes().decode("utf-8")
    except UnicodeError as exc:
        raise BmError(f"bookmark is not valid UTF-8: {relative!r}") from exc
    return {"path": relative, "content": content}


def write_backup(store: Store, stream: TextIO) -> None:
    """Serialize every bookmark, failing rather than silently skipping files."""
    store.require_exists()
    records = []

    def fail(exc: OSError) -> None:
        raise exc

    for root, dirs, files in os.walk(store.root, onerror=fail):
        in_git = is_reserved_path(Path(root).relative_to(store.root).as_posix())
        if not in_git:
            for name in dirs:
                if name.casefold() != ".git" and (Path(root) / name).is_symlink():
                    raise UnsafePathError(f"symlink directory in store: {Path(root) / name}")
        for name in files:
            if not name.endswith(".bm"):
                continue
            relative = (Path(root) / name).relative_to(store.root).as_posix()
            if in_git:
                raise UnsafePathError(
                    f"bookmark in reserved namespace: {relative}; "
                    "move it outside Git metadata before backing up"
                )
            records.append(_backup_record(store, relative))
    records.sort(key=lambda record: record["path"])
    json.dump(
        {"format": BACKUP_FORMAT, "version": BACKUP_VERSION, "entries": records},
        stream,
        ensure_ascii=False,
        indent=2,
    )
    stream.write("\n")


def build_backup(store: Store) -> str:
    """Return a backup string for embedding; CLI writers use the stream API."""
    stream = io.StringIO()
    write_backup(store, stream)
    return stream.getvalue()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise BmError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def read_backup(text: str) -> list[dict[str, str]]:
    """Decode the supported backup envelope, never accepting pipeline exports."""
    try:
        document = json.loads(text, object_pairs_hook=_unique_object)
    except ValueError as exc:
        raise BmError(f"invalid backup JSON: {exc}") from exc
    if not isinstance(document, dict) or document.get("format") != BACKUP_FORMAT:
        raise BmError("not a lossless backup; create one with 'bm export -o bookmarks.json'")
    if type(document.get("version")) is not int or document["version"] != BACKUP_VERSION:
        raise BmError(f"unsupported backup version: {document.get('version')!r}")
    if set(document) != {"format", "version", "entries"}:
        raise BmError("unexpected backup fields")
    entries = document["entries"]
    if not isinstance(entries, list):
        raise BmError("backup entries must be an array")
    for index, entry in enumerate(entries, 1):
        if (
            not isinstance(entry, dict)
            or set(entry) != {"path", "content"}
            or not all(isinstance(value, str) for value in entry.values())
        ):
            raise BmError(f"malformed backup record {index}: expected path and content strings")
    return entries


@dataclass
class RestoreItem:
    """A validated destination and its optimistic precondition."""

    path: Path
    content: bytes
    previous: bytes | None


@dataclass
class RestorePlan:
    """A complete preflight; no directories or files are created by planning."""

    items: list[RestoreItem]
    unchanged: int
    conflicts: list[str]

    def summary(self, *, preview: bool) -> str:
        added = sum(item.previous is None for item in self.items)
        replaced = len(self.items) - added
        if preview:
            return (
                f"Would add {added}, replace {replaced}, leave unchanged {self.unchanged}; "
                f"conflicts {len(self.conflicts)}.\nNo files written."
            )
        return f"Added {added}, replaced {replaced}, left unchanged {self.unchanged}."


def plan_restore(
    store: Store, entries: list[dict[str, str]], schemes: frozenset[str], *, force: bool
) -> RestorePlan:
    """Validate all records and destinations before permitting any mutation."""
    if store.root.exists() and not store.root.is_dir():
        raise BmError(f"store is not a directory: {store.root}")
    plan = RestorePlan([], 0, [])
    seen: set[Path] = set()
    ancestors: set[Path] = set()
    for entry in sorted(entries, key=lambda record: record["path"]):
        name = entry["path"]
        path = store.exact_path(name)
        destination = path.resolve()
        if destination in seen:
            raise BmError(f"duplicate backup destination: {name!r}")
        if destination in ancestors or any(parent in seen for parent in destination.parents):
            raise BmError(f"overlapping backup destinations: {name!r}")
        seen.add(destination)
        ancestors.update(destination.parents)
        try:
            content = entry["content"].encode("utf-8")
            meta, _ = parse_front_matter(entry["content"])
            inspection = inspect_url(meta.get("url", ""), schemes)
        except (UnicodeError, ValueError) as exc:
            raise BmError(f"invalid bookmark {name!r}: {exc}") from exc
        # Exact restore preserves supported-scheme content, including malformed
        # hosts/ports. Doctor diagnoses structure; browser import rejects it.
        if not inspection.supported:
            raise BmError(f"missing or unsupported URL scheme in {name!r}")
        if path.exists() and not path.is_file():
            raise BmError(f"destination is not a regular file: {name!r}")
        previous = store.snapshot(path) if path.exists() else None
        if previous == content:
            plan.unchanged += 1
        elif previous is not None and not force:
            plan.conflicts.append(name)
        else:
            plan.items.append(RestoreItem(path, content, previous))
    return plan


def apply_restore(store: Store, plan: RestorePlan) -> None:
    """Publish each file safely; this is not a whole-store transaction."""
    if plan.conflicts:
        raise BmError("restore has unresolved conflicts")
    for item in plan.items:
        name = item.path.relative_to(store.root).as_posix()
        store.exact_path(name)
        if item.previous is None:
            store.create_entry(item.path, item.content)
        else:
            store.write(item.path, item.content, expected=item.previous)
