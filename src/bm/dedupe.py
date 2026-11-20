"""Duplicate merge rules, explicit plans, and optimistic filesystem execution."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set, Tuple

from .errors import BmError, ConflictError
from .io import build_text
from .models import Metadata, metadata_tags, metadata_text
from .paths import bookmark_token
from .store import Store, StoreEntry
from .urls import parse_stored_url
from .utils import iso_now, normalize_url_for_compare, parse_iso


def _entry_score(entry: StoreEntry) -> Tuple[int, int, float, str]:
    """Return the stable score used to choose a duplicate survivor."""
    body_len = len(entry.body.strip())
    title_len = len(metadata_text(entry.meta, "title").strip())
    created_dt = parse_iso(metadata_text(entry.meta, "created")) or parse_iso(
        metadata_text(entry.meta, "modified")
    )
    created_ts = created_dt.timestamp() if created_dt else float("inf")
    return (-body_len, -title_len, created_ts, entry.relative_path.as_posix())


def select_survivor(entries: List[StoreEntry]) -> StoreEntry:
    """Choose the deterministic survivor from a normalized duplicate group."""
    return min(entries, key=_entry_score)


def _earliest_dt(current: Optional[datetime], candidate: Optional[datetime]) -> Optional[datetime]:
    if candidate is None:
        return current
    return min(current, candidate) if current is not None else candidate


def _latest_dt(current: Optional[datetime], candidate: Optional[datetime]) -> Optional[datetime]:
    if candidate is None:
        return current
    return max(current, candidate) if current is not None else candidate


def _collect_group_stats(
    entries: List[StoreEntry],
) -> Tuple[Set[str], Optional[datetime], Optional[datetime], List[str]]:
    """Collect unioned tags, date bounds, and candidate titles for a group."""
    tags_union: Set[str] = set()
    earliest_created: Optional[datetime] = None
    latest_modified: Optional[datetime] = None
    title_candidates: List[str] = []

    for entry in entries:
        meta = entry.meta
        tags_union.update(t.strip() for t in metadata_tags(meta) if t.strip())
        tags_union.update(seg for seg in entry.relative_path.parts[:-1] if seg)

        title = metadata_text(meta, "title").strip()
        if title:
            title_candidates.append(title)

        earliest_created = _earliest_dt(earliest_created, parse_iso(metadata_text(meta, "created")))
        latest_modified = _latest_dt(latest_modified, parse_iso(metadata_text(meta, "modified")))

    return tags_union, earliest_created, latest_modified, title_candidates


def _collect_body_parts(entries: List[StoreEntry], survivor: StoreEntry) -> Tuple[List[str], bool]:
    """Collect the survivor body and non-empty duplicate notes."""
    base_body = survivor.body.rstrip()
    parts: List[str] = [base_body] if base_body else []
    notes_appended = False

    for entry in entries:
        if entry is survivor:
            continue
        extra_body = entry.body.rstrip()
        if extra_body:
            notes_appended = True
            parts.append(f"[Merged from {entry.relative_path.as_posix()}]\n{extra_body}".rstrip())

    return parts, notes_appended


def _join_body_parts(parts: List[str]) -> str:
    """Join merged note sections with one trailing newline."""
    clean = [part for part in parts if part]
    if not clean:
        return ""
    merged = "\n\n".join(clean)
    return merged.rstrip() + "\n"


@dataclass(frozen=True)
class MergeResult:
    """Proposed content and provenance, separate from every input snapshot."""

    meta: Metadata
    body: str
    notes_appended: bool
    earliest_created: Optional[datetime]
    latest_modified: Optional[datetime]


def merge_entry_group(entries: List[StoreEntry], survivor: StoreEntry) -> MergeResult:
    """Merge metadata and notes without mutating the input entries."""
    merged_meta = dict(survivor.meta)
    tags_union, earliest_created, latest_modified, title_candidates = _collect_group_stats(entries)

    if title_candidates and not metadata_text(merged_meta, "title"):
        title_candidates.sort(key=len, reverse=True)
        merged_meta["title"] = title_candidates[0]

    if earliest_created:
        merged_meta["created"] = earliest_created.isoformat()

    if latest_modified:
        merged_meta["modified"] = latest_modified.isoformat()

    body_parts, notes_appended = _collect_body_parts(entries, survivor)
    tags = sorted(t for t in tags_union if t)
    merged_body = _join_body_parts(body_parts)

    merged_meta["tags"] = tags
    return MergeResult(merged_meta, merged_body, notes_appended, earliest_created, latest_modified)


@dataclass(frozen=True)
class DuplicatePlan:
    """One group's proposed merge, used unchanged for preview and execution."""

    canonical_url: str
    survivor: StoreEntry
    members: tuple[StoreEntry, ...]
    merged: MergeResult

    def document(self, *, dry_run: bool) -> dict:
        action = {
            "canonical_url": self.canonical_url,
            "kept": self.survivor.relative_path.as_posix(),
            "removed": [
                entry.relative_path.as_posix()
                for entry in self.members
                if entry is not self.survivor
            ],
            "tags": list(metadata_tags(self.merged.meta)),
            "notes_appended": self.merged.notes_appended,
            "total": len(self.members),
        }
        if self.merged.earliest_created:
            action["created"] = self.merged.earliest_created.isoformat()
        if self.merged.latest_modified:
            action["latest_modified"] = self.merged.latest_modified.isoformat()
        if dry_run:
            action["dry_run"] = True
        return action


@dataclass(frozen=True)
class DedupePlan:
    """A best-effort scan followed by explicit duplicate-group operations."""

    groups: tuple[DuplicatePlan, ...]
    scanned: int


def duplicate_key(url: str) -> str:
    """Use the legacy comparison key, but don't merge structurally damaged URLs."""
    parse_stored_url(url)
    return normalize_url_for_compare(url)


def _load_candidates(
    store: Store,
    paths: list[Path],
    canonical: str,
    on_error: Callable[[Path, Exception], None] | None,
) -> list[StoreEntry]:
    members = []
    for path in paths:
        try:
            entry = store.read_entry(path, snapshot=True)
            key = duplicate_key(metadata_text(entry.meta, "url"))
        except (OSError, BmError, ValueError) as exc:
            if on_error is not None:
                on_error(bookmark_token(path.relative_to(store.root)), exc)
            continue
        if key != canonical:
            raise ConflictError(f"bookmark URL changed during dedupe scan: {path}; rerun dedupe")
        members.append(entry)
    return members


def plan_dedupe(
    store: Store,
    *,
    on_error: Callable[[Path, Exception], None] | None = None,
    on_progress: Callable[[int], None] | None = None,
) -> DedupePlan:
    """Group by header URL, then snapshot and revalidate only possible duplicates."""
    buckets: dict[str, list[Path]] = {}
    scanned = 0
    for entry in store.iter_entries(meta_only=True, on_error=on_error):
        try:
            key = duplicate_key(metadata_text(entry.meta, "url"))
        except ValueError as exc:
            if on_error is not None:
                on_error(entry.relative_path, exc)
            continue
        if not key:
            continue
        buckets.setdefault(key, []).append(entry.path)
        scanned += 1
        if on_progress:
            on_progress(scanned)
    groups = []
    for canonical, paths in buckets.items():
        if len(paths) < 2:
            continue
        members = _load_candidates(store, paths, canonical, on_error)
        if len(members) < 2:
            continue
        survivor = select_survivor(members)
        groups.append(
            DuplicatePlan(canonical, survivor, tuple(members), merge_entry_group(members, survivor))
        )
    return DedupePlan(tuple(groups), scanned)


def _snapshot(entry: StoreEntry) -> bytes:
    if entry.snapshot is None:
        raise ValueError("dedupe execution requires original byte snapshots")
    return entry.snapshot


def apply_dedupe(store: Store, plan: DedupePlan) -> None:
    """Apply each group safely; like restore, this is not a whole-store transaction."""
    for group in plan.groups:
        for entry in group.members:
            store.verify(entry.path, _snapshot(entry))
        meta = dict(group.merged.meta)
        if not metadata_text(meta, "modified"):
            meta["modified"] = iso_now()
        store.write(
            group.survivor.path,
            build_text(meta, group.merged.body),
            expected=_snapshot(group.survivor),
        )
        for entry in group.members:
            if entry is not group.survivor:
                store.delete(entry.path, expected=_snapshot(entry))
