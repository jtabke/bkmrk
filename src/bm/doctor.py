"""Read-only filesystem diagnostics; findings never authorize automatic repair."""

import os
import stat
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .errors import UnsafePathError
from .io import parse_front_matter
from .paths import is_reserved_path
from .store import Store
from .urls import inspect_url
from .utils import normalize_url_for_compare, rid


@dataclass
class Finding:
    """One deterministic diagnostic with a manual recovery hint."""

    code: str
    severity: str
    paths: list[str]
    message: str
    hint: str


@dataclass
class DoctorReport:
    """Structured store health results shared by human and JSON presentations."""

    store: str
    checked: int = 0
    findings: list[Finding] = field(default_factory=list)

    def add(
        self, code: str, paths: list[str], message: str, hint: str, *, warning: bool = False
    ) -> None:
        self.findings.append(
            Finding(code, "warning" if warning else "error", sorted(paths), message, hint)
        )

    def document(self) -> dict:
        return {
            "store": self.store,
            "checked": self.checked,
            "ok": not self.findings,
            "findings": [
                asdict(item)
                for item in sorted(self.findings, key=lambda item: (item.paths, item.code))
            ],
        }


def _inspect_bookmark(
    report: DoctorReport, name: str, raw: bytes, schemes: frozenset[str]
) -> str | None:
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        report.add(
            "invalid_utf8",
            [name],
            "Bookmark is not valid UTF-8.",
            "Preserve a copy, then correct the encoding in an editor.",
        )
        return None
    diagnostics: list[str] = []
    meta, _ = parse_front_matter(text, diagnostics=diagnostics)
    if diagnostics:
        report.add(
            "invalid_front_matter",
            [name],
            diagnostics[0],
            "Preserve a copy; correct the header without discarding notes or unknown fields.",
        )
    url = meta.get("url", "").strip()
    if not url:
        report.add(
            "missing_url",
            [name],
            "Bookmark has no URL.",
            "Restore the URL from a trusted copy or edit this bookmark manually.",
        )
        return None
    inspection = inspect_url(url, schemes)
    if inspection.problem:
        report.add(
            "invalid_url",
            [name],
            f"Malformed URL: {inspection.problem}.",
            "Correct the URL manually; doctor does not fetch or open it.",
        )
        return None
    if not inspection.supported:
        report.add(
            "unsupported_url_scheme",
            [name],
            "URL scheme is missing or unsupported.",
            "Check the saved URL; do not open an untrusted scheme.",
        )
        return None
    return url


def _inspect_file(
    store: Store, path: Path, report: DoctorReport, schemes: frozenset[str]
) -> tuple[str | None, os.stat_result] | None:
    name = path.relative_to(store.root).as_posix()
    bookmark = path.suffix == ".bm"
    temporary = path.suffix in {".tmp", ".edit"}
    if not bookmark and not temporary:
        return None
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise UnsafePathError("symlink file is not inspected")
        if not stat.S_ISREG(info.st_mode):
            raise UnsafePathError("not a regular file")
        if temporary:
            code = "temporary_hardlink" if info.st_nlink > 1 else "temporary_file"
            report.add(
                code,
                [name],
                "Possible interrupted write/editor temporary file"
                + (" with multiple hard links." if info.st_nlink > 1 else "."),
                "Check for active writers; compare with bookmarks before removing anything.",
                warning=True,
            )
            return None
        report.checked += 1
        store.exact_path(name)
        url = _inspect_bookmark(report, name, path.read_bytes(), schemes)
        return url, info
    except (OSError, UnsafePathError, ValueError) as exc:
        report.add(
            "unsafe_or_unreadable_path",
            [name],
            str(exc),
            "Inspect permissions and links manually; do not follow untrusted symlinks.",
        )
        return None


def _walk_files(store: Store, report: DoctorReport) -> Iterator[Path]:
    """Walk only owned directories and retain read failures as findings."""

    def walk_error(exc: OSError) -> None:
        report.add(
            "unreadable_directory",
            [str(exc.filename)],
            str(exc),
            "Inspect directory permissions manually, then rerun doctor.",
        )

    for root, dirs, files in os.walk(store.root, onerror=walk_error):
        in_git = is_reserved_path(Path(root).relative_to(store.root).as_posix())
        for name in sorted(dirs.copy()):
            path = Path(root) / name
            if path.is_symlink():
                dirs.remove(name)
                if not in_git and name.casefold() != ".git":
                    report.add(
                        "symlink_directory",
                        [path.relative_to(store.root).as_posix()],
                        "Symlink directory is not inspected.",
                        "Inspect the link manually; keep bookmark directories inside the store.",
                    )
        for name in sorted(files):
            path = Path(root) / name
            if in_git:
                if path.suffix == ".bm":
                    report.add(
                        "reserved_bookmark_path",
                        [path.relative_to(store.root).as_posix()],
                        "Bookmark is inside reserved Git metadata and cannot be backed up.",
                        "Preserve a copy and move the bookmark outside every .git directory.",
                    )
            else:
                yield path


def inspect_store(store: Store, schemes: frozenset[str]) -> DoctorReport:
    """Inspect bookmark files without creating a store or following symlinks."""
    report = DoctorReport(str(store.root))
    if not store.root.is_dir():
        report.add(
            "missing_store",
            ["."],
            "Store is missing or is not a directory.",
            "Check --store or BOOKMARKS_DIR; use 'bm init' only to create a new store.",
        )
        return report
    ids: dict[str, list[str]] = defaultdict(list)
    urls: dict[str, list[str]] = defaultdict(list)
    inodes: dict[tuple[int, int], list[str]] = defaultdict(list)

    for path in _walk_files(store, report):
        result = _inspect_file(store, path, report, schemes)
        if result is None:
            continue
        url, info = result
        relative = path.relative_to(store.root).as_posix()
        if url:
            ids[rid(url)].append(relative)
            urls[normalize_url_for_compare(url)].append(relative)
        if info.st_ino and info.st_nlink > 1:
            inodes[(info.st_dev, info.st_ino)].append(relative)
    for code, groups, message, hint in (
        (
            "duplicate_id",
            ids,
            "Bookmarks share a stable URL-derived ID.",
            "Compare paths and notes before choosing which bookmark to keep.",
        ),
        (
            "duplicate_url",
            urls,
            "Bookmarks have equivalent normalized URLs.",
            "Review 'bm dedupe --dry-run' before authorizing a merge.",
        ),
        (
            "possible_interrupted_move",
            inodes,
            "Bookmark paths share the same hard-linked file.",
            "Check for active writers and compare paths; do not delete either name blindly.",
        ),
    ):
        for paths in groups.values():
            if len(paths) > 1:
                report.add(code, paths, message, hint, warning=True)
    return report
