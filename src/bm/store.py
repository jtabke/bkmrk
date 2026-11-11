"""Concrete filesystem boundary for bookmark stores."""

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Generator, Optional, Union

from .errors import AmbiguousEntryError, ConflictError, NotFoundError, UnsafePathError
from .io import (
    atomic_create,
    atomic_move_no_replace,
    atomic_write,
    load_entry,
    parse_front_matter,
    verify_snapshot,
)
from .models import FILE_EXT, Metadata, metadata_text
from .paths import (
    id_to_path,
    matches_path_prefix,
    normalize_path_prefix,
    normalize_slug,
    reject_reserved_namespace,
    reject_unsafe,
    reject_windows_names,
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

    def exact_path(self, name: str) -> Path:
        """Validate an exact bookmark filename without slug normalization or links."""
        if not name or "\\" in name or reject_unsafe(name) != name or not name.endswith(FILE_EXT):
            raise UnsafePathError(f"unsafe bookmark path: {name!r}")
        reject_reserved_namespace(name)
        if sys.platform == "win32":
            reject_windows_names(name)
        path = self._check_member(self.root / name)
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

    def read_entry(self, path: Path, *, snapshot: bool = False) -> StoreEntry:
        """Load an entry, optionally retaining the exact bytes read."""
        checked = self._check_member(path)
        if snapshot:
            raw = checked.read_bytes()
            meta, body = parse_front_matter(raw.decode("utf-8", errors="replace"))
            raw_snapshot: Optional[bytes] = raw
        else:
            meta, body = load_entry(checked)
            raw_snapshot = None
        return StoreEntry(
            path=checked,
            relative_path=checked.relative_to(self.root).with_suffix(""),
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

        exact = self.root / (prefix + FILE_EXT)
        if exact.exists() or exact.is_symlink():
            yield exact
        if directory.is_dir():
            yield from directory.rglob(f"*{FILE_EXT}")

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
        if not self.exists():
            return
        prefix = normalize_path_prefix(path_prefix)
        for path in self._iter_paths(prefix):
            relative = path.relative_to(self.root).with_suffix("")
            if prefix and not matches_path_prefix(relative, prefix):
                continue
            try:
                if snapshot:
                    entry = self.read_entry(path, snapshot=True)
                elif meta_only:
                    checked = self._check_member(path)
                    meta, _ = load_entry(checked, meta_only=True)
                    entry = StoreEntry(checked, relative, meta, "")
                else:
                    entry = self.read_entry(path)
            except (OSError, UnsafePathError, ValueError, UnicodeError) as exc:
                if on_error is not None:
                    on_error(relative, exc)
                continue
            yield entry

    def _single_match(self, matches: list[Path], token: str) -> Path | None:
        """Resolve one match or require an explicit path when lookup is ambiguous."""
        if len(matches) > 1:
            listing = "\n  ".join(
                path.relative_to(self.root).with_suffix("").as_posix() for path in sorted(matches)
            )
            raise AmbiguousEntryError(
                f"ambiguous: {len(matches)} matches for {token!r}:\n  {listing}\n"
                "Use a complete path from the list above."
            )
        return matches[0] if matches else None

    def resolve(self, token: str) -> Optional[Path]:
        """Resolve an ID or path token using the existing command priority."""
        token = token.strip()
        slug = normalize_slug(reject_unsafe(token))
        slug = reject_unsafe(slug)
        exact = self.path_for(slug)
        if exact.exists():
            return exact

        name = Path(slug).name
        fuzzy = []
        for path in self.root.rglob(f"*{FILE_EXT}"):
            if name not in path.stem:
                continue
            try:
                fuzzy.append(self._check_member(path))
            except UnsafePathError:
                continue
        if fuzzy:
            return self._single_match(fuzzy, token)

        matches = []
        for entry in self.iter_entries(meta_only=True):
            url = metadata_text(entry.meta, "url")
            if url and rid(url) == token:
                matches.append(entry.path)
        return self._single_match(matches, token)
