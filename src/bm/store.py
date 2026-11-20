"""Concrete filesystem boundary for bookmark stores."""

import re
import stat
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Generator, Optional, Union

from .errors import AmbiguousEntryError, BmError, ConflictError, NotFoundError, UnsafePathError
from .io import (
    atomic_create,
    atomic_move_no_replace,
    atomic_write,
    load_entry,
    parse_for_rewrite,
    verify_snapshot,
)
from .models import FILE_EXT, Metadata, metadata_text
from .paths import (
    bookmark_filename,
    bookmark_token,
    id_to_path,
    matches_path_prefix,
    normalize_path_prefix,
    normalize_slug,
    reject_reserved_namespace,
    reject_unsafe,
    validate_bookmark_name,
)
from .utils import rid


@dataclass
class StoreEntry:
    """One parsed bookmark and the path that owns it."""

    path: Path
    relative_path: Path
    meta: Metadata
    body: str
    snapshot: Optional[bytes] = None


class Store:
    """Filesystem-backed bookmark store.

    This is intentionally a concrete boundary rather than a repository
    abstraction. It centralizes path containment, enumeration, and mutation
    preconditions while leaving front-matter parsing in :mod:`bm.io`.
    """

    def __init__(self, root: Union[Path, str]):
        self.root = Path(root)

    def exists(self) -> bool:
        """Return whether the store directory exists."""
        return self.root.is_dir()

    def check_root(self) -> None:
        """Reject a root blocked by a non-directory without creating anything."""
        for parent in (self.root, *self.root.parents):
            if (parent.exists() or parent.is_symlink()) and not parent.is_dir():
                raise UnsafePathError(
                    f"store path is blocked by a non-directory: {parent}; "
                    "choose another --store path"
                )

    def create(self) -> Path:
        """Create the store directory with restrictive permissions."""
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        return self.root

    def require_exists(self, message: Optional[str] = None) -> Path:
        """Return the root or raise a domain error when it is missing."""
        if not self.exists():
            raise NotFoundError(message or f"store not found: {self.root}")
        return self.root

    def _check_member(self, path: Path) -> Path:
        """Return ``path`` after ensuring it is contained by this store."""
        candidate = Path(path)
        try:
            resolved_relative = candidate.resolve().relative_to(self.root.resolve())
        except ValueError as exc:
            raise UnsafePathError(f"path is outside store: {candidate}") from exc
        reject_reserved_namespace(resolved_relative.as_posix())
        # Check the lexical name as well: an inward link inside .git must not
        # make a reserved destination look like an ordinary bookmark path.
        if candidate.absolute().is_relative_to(self.root.absolute()):
            relative = candidate.absolute().relative_to(self.root.absolute())
            reject_reserved_namespace(relative.as_posix())
        return candidate

    def path_for(self, slug: str) -> Path:
        """Build a safe store-relative bookmark path from user input."""
        return self._check_member(id_to_path(self.root, slug))

    def _literal_path(self, name: str) -> Path:
        """Validate the filename as written, without slug normalization."""
        validate_bookmark_name(name)
        return self._check_member(self.root / name)

    def exact_path(self, name: str) -> Path:
        """Validate an exact backup/restore path, including its no-links policy."""
        path = self._literal_path(name)
        self.check_destination(path)
        return path

    def check_destination(self, path: Path) -> None:
        """Reject linked or blocked destinations without creating directories."""
        checked = self._check_member(path)
        current = self.root
        for part in checked.relative_to(self.root).parts:
            current = current / part
            if current.is_symlink():
                raise UnsafePathError(f"symlink in bookmark destination: {current}")
            if not current.exists():
                continue
            if current == checked:
                if not current.is_file():
                    raise UnsafePathError(f"destination is not a regular file: {current}")
            elif not current.is_dir():
                raise UnsafePathError(f"parent is not a directory: {current}")

    def require_entry(self, token: str) -> Path:
        """Resolve a bookmark or report the shared actionable not-found diagnostic."""
        path = self.resolve(token)
        if path is None:
            raise NotFoundError(
                f"bookmark not found: {token!r}; use 'bm search <terms>' or 'bm list' "
                "to find an ID or path"
            )
        return path

    def ensure_parent(self, path: Path) -> Path:
        """Create a bookmark path's parent directory after containment checks."""
        checked = self._check_member(path)
        checked.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        return checked

    def snapshot(self, path: Path) -> bytes:
        """Read the exact bytes used as an optimistic mutation precondition."""
        return self._check_member(path).read_bytes()

    def verify(self, path: Path, expected: bytes) -> None:
        """Verify a member still matches a previously captured snapshot."""
        verify_snapshot(self._check_member(path), expected)

    def write(self, path: Path, data: str | bytes, expected: Optional[bytes] = None) -> None:
        """Atomically replace a member, optionally requiring an unchanged snapshot."""
        checked = self.ensure_parent(path)
        atomic_write(checked, data, expected=expected)

    def create_entry(self, path: Path, data: str | bytes) -> None:
        """Atomically create a member without replacing an existing destination."""
        checked = self.ensure_parent(path)
        atomic_create(checked, data)

    def delete(self, path: Path, expected: Optional[bytes] = None) -> None:
        """Delete a member, optionally requiring an unchanged snapshot."""
        checked = self._check_member(path)
        if expected is not None:
            self.verify(checked, expected)
        try:
            checked.unlink()
        except FileNotFoundError as exc:
            if expected is not None:
                raise ConflictError(f"bookmark changed since it was read: {checked}") from exc
            raise
        self.prune_empty_dirs(checked.parent)

    def move(
        self,
        source: Path,
        destination: Path,
        *,
        expected: Optional[bytes] = None,
        force: bool = False,
    ) -> Path:
        """Move a member, checking its source snapshot before replacement."""
        source = self._check_member(source)
        destination = self.ensure_parent(destination)
        if source.is_symlink():
            raise OSError(f"refusing to move a symlink: {source}")
        if expected is not None:
            self.verify(source, expected)
        source_parent = source.parent
        if force:
            source.replace(destination)
        else:
            atomic_move_no_replace(source, destination)
        self.prune_empty_dirs(source_parent)
        return destination

    def prune_empty_dirs(self, start: Path) -> None:
        """Remove empty member directories up to, but not including, the root."""
        root = self.root.resolve()
        current = self._check_member(start).resolve()
        while current != root and current.exists() and not any(current.iterdir()):
            current.rmdir()
            current = current.parent

    @staticmethod
    def _require_file(checked: Path) -> Path:
        if not stat.S_ISREG(checked.stat().st_mode):
            raise UnsafePathError(f"not a regular bookmark file: {checked}")
        return checked

    def read_entry(self, path: Path, *, snapshot: bool = False) -> StoreEntry:
        """Load an entry, strictly decoding snapshots intended for a rewrite."""
        checked = self._require_file(self._check_member(path))
        return self._load_checked(checked, meta_only=False, snapshot=snapshot)

    def _load_checked(self, checked: Path, *, meta_only: bool, snapshot: bool) -> StoreEntry:
        raw_snapshot = None
        if snapshot:
            raw_snapshot = checked.read_bytes()
            meta, body = parse_for_rewrite(raw_snapshot, checked)
        else:
            meta, body = load_entry(checked, meta_only=meta_only)
        return StoreEntry(
            path=checked,
            relative_path=bookmark_token(checked.relative_to(self.root)),
            meta=meta,
            body=body,
            snapshot=raw_snapshot,
        )

    def _iter_paths(self, prefix: str) -> Generator[Path, None, None]:
        """Narrow scans to literal prefixes without following directory symlinks."""
        if not prefix:
            yield from self.root.rglob(f"*{FILE_EXT}")
            return
        try:
            safe_prefix = reject_unsafe(prefix)
        except UnsafePathError:
            safe_prefix = ""
        if safe_prefix != prefix:
            # Unusual filters retain their original string-matching semantics.
            yield from self.root.rglob(f"*{FILE_EXT}")
            return

        directory = self.root
        for part in prefix.split("/"):
            directory = directory / part
            if directory.is_symlink():
                # rglob does not recurse into directory symlinks. Starting at
                # one would change that behavior, even for an inward link.
                yield from self.root.rglob(f"*{FILE_EXT}")
                return

        exact = self.root / bookmark_filename(prefix)
        if exact.exists() or exact.is_symlink():
            yield exact
        if directory.is_dir():
            yield from directory.rglob(f"*{FILE_EXT}")

    def _checked_paths(
        self, paths: Iterable[Path], on_error: Callable[[Path, Exception], None] | None = None
    ) -> Iterator[Path]:
        for path in paths:
            relative = path.relative_to(self.root)
            try:
                # A literal POSIX backslash, for instance, must not be displayed
                # as a lookup token that would select a different bookmark.
                checked = self._require_file(self._literal_path(relative.as_posix()))
            except (OSError, UnsafePathError, ValueError) as exc:
                if on_error is not None:
                    on_error(bookmark_token(relative), exc)
                continue
            yield checked

    def iter_paths(
        self, *, path_prefix: str = "", on_error: Callable[[Path, Exception], None] | None = None
    ) -> Iterator[Path]:
        """Yield regular member paths, checking containment without opening bookmarks."""
        if not self.exists():
            return
        prefix = normalize_path_prefix(path_prefix)
        paths: Iterator[Path] = self._iter_paths(prefix)
        if prefix:
            paths = (
                p
                for p in paths
                if matches_path_prefix(bookmark_token(p.relative_to(self.root)), prefix)
            )
        yield from self._checked_paths(paths, on_error)

    def iter_entries(
        self,
        *,
        meta_only: bool = False,
        snapshot: bool = False,
        path_prefix: str = "",
        on_error: Optional[Callable[[Path, Exception], None]] = None,
    ) -> Generator[StoreEntry, None, None]:
        """Yield parsed bookmark entries, optionally with byte snapshots.

        Malformed/unreadable entries are skipped to preserve the CLI's existing
        best-effort scan behavior. Callers can provide ``on_error`` to retain
        the warning presentation at the CLI boundary. ``path_prefix`` selects
        extensionless relative paths before file reads and avoids traversing
        unrelated directories when safe.
        """
        for path in self.iter_paths(path_prefix=path_prefix, on_error=on_error):
            try:
                entry = self._load_checked(path, meta_only=meta_only, snapshot=snapshot)
            except (OSError, BmError, ValueError) as exc:
                if on_error is not None:
                    on_error(bookmark_token(path.relative_to(self.root)), exc)
                continue
            yield entry

    def _single_match(self, matches: list[Path], token: str) -> Path | None:
        """Resolve one match or require an explicit path when lookup is ambiguous."""
        if len(matches) > 1:
            listing = "\n  ".join(
                bookmark_token(path.relative_to(self.root)).as_posix() for path in sorted(matches)
            )
            raise AmbiguousEntryError(
                f"ambiguous: {len(matches)} matches for {token!r}:\n  {listing}\n"
                "Use a complete path from the list above."
            )
        return matches[0] if matches else None

    def _id_matches(self, paths: Iterable[Path], token: str) -> list[Path]:
        matches = []
        # Validate each buffered name at read time, not just at discovery time.
        for path in self._checked_paths(paths):
            try:
                meta, _ = load_entry(path, meta_only=True)
            except (OSError, ValueError):
                continue
            url = metadata_text(meta, "url")
            if url and rid(url) == token:
                matches.append(path)
        return matches

    def _existing_file(self, name: str) -> Path | None:
        path = self._literal_path(name)
        try:
            return self._require_file(path)
        except (FileNotFoundError, NotADirectoryError):
            if path.is_symlink():
                raise UnsafePathError(f"broken bookmark link: {path}") from None
            return None

    def resolve(self, token: str) -> Optional[Path]:
        """Prefer the literal listed path, then the legacy slug, partial name, and ID."""
        logical = reject_unsafe(token)
        literal = bookmark_filename(logical) if logical else ""
        if literal and (exact := self._existing_file(literal)):
            return exact

        token = token.strip()
        slug = reject_unsafe(normalize_slug(reject_unsafe(token)))
        if slug + FILE_EXT != literal and (exact := self._existing_file(slug + FILE_EXT)):
            return exact

        name = Path(slug).name
        is_id = re.fullmatch(r"[0-9a-f]{12}", token) is not None
        paths: Iterable[Path] = self._iter_paths("")
        if is_id:
            # Only ID-shaped tokens might need headers. Walk once and preserve
            # partial-name priority without opening anything if a name matches.
            paths = list(paths)
        matches = list(self._checked_paths(p for p in paths if name in p.stem))
        if is_id and not matches:
            matches = self._id_matches(paths, token)
        return self._single_match(matches, token)
