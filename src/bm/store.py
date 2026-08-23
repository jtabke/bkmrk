"""Concrete filesystem boundary for bookmark stores."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Generator, Optional, Union

from .errors import AmbiguousEntryError, ConflictError, NotFoundError, UnsafePathError
from .io import atomic_create, atomic_move_no_replace, atomic_write, load_entry, parse_front_matter
from .models import FILE_EXT
from .utils import _reject_absolute_path, _reject_unsafe, id_to_path, normalize_slug, rid


@dataclass
class StoreEntry:
    """One parsed bookmark and the path that owns it."""

    path: Path
    relative_path: Path
    meta: Dict[str, Any]
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
            candidate.resolve().relative_to(self.root.resolve())
        except ValueError as exc:
            raise UnsafePathError(f"path is outside store: {candidate}") from exc
        return candidate

    def path_for(self, slug: str) -> Path:
        """Build a safe store-relative bookmark path from user input."""
        return self._check_member(id_to_path(self.root, slug))

    def ensure_parent(self, path: Path) -> Path:
        """Create a bookmark path's parent directory after containment checks."""
        checked = self._check_member(path)
        checked.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        return checked

    def snapshot(self, path: Path) -> bytes:
        """Read the exact bytes used as an optimistic mutation precondition."""
        return self._check_member(path).read_bytes()

    def _check_snapshot(self, path: Path, expected: bytes) -> None:
        """Raise a conflict when a path no longer matches its read snapshot."""
        checked = self._check_member(path)
        try:
            current = checked.read_bytes()
        except FileNotFoundError as exc:
            raise ConflictError(f"bookmark changed since it was read: {checked}") from exc
        if current != expected:
            raise ConflictError(f"bookmark changed since it was read: {checked}")

    def verify(self, path: Path, expected: bytes) -> None:
        """Verify a member still matches a previously captured snapshot."""
        self._check_snapshot(path, expected)

    def write(self, path: Path, data: str, expected: Optional[bytes] = None) -> None:
        """Atomically replace a member, optionally requiring an unchanged snapshot."""
        checked = self.ensure_parent(path)
        atomic_write(checked, data, expected=expected)

    def create_entry(self, path: Path, data: str) -> None:
        """Atomically create a member without replacing an existing destination."""
        checked = self.ensure_parent(path)
        atomic_create(checked, data)

    def delete(self, path: Path, expected: Optional[bytes] = None) -> None:
        """Delete a member, optionally requiring an unchanged snapshot."""
        checked = self._check_member(path)
        if expected is not None:
            self._check_snapshot(checked, expected)
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
            self._check_snapshot(source, expected)
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

    def iter_entries(
        self,
        *,
        meta_only: bool = False,
        snapshot: bool = False,
        on_error: Optional[Callable[[Path, Exception], None]] = None,
    ) -> Generator[StoreEntry, None, None]:
        """Yield parsed bookmark entries, optionally with byte snapshots.

        Malformed/unreadable entries are skipped to preserve the CLI's existing
        best-effort scan behavior. Callers can provide ``on_error`` to retain
        the warning presentation at the CLI boundary.
        """
        if not self.exists():
            return
        for path in self.root.rglob(f"*{FILE_EXT}"):
            relative = path.relative_to(self.root).with_suffix("")
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

    def resolve(self, token: str) -> Optional[Path]:
        """Resolve an ID or path token using the existing command priority."""
        token = token.strip()
        _reject_absolute_path(token)
        slug = normalize_slug(token)
        slug = _reject_unsafe(slug)
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
        if len(fuzzy) == 1:
            return fuzzy[0]
        if len(fuzzy) > 1:
            listing = "\n  ".join(
                str(path.relative_to(self.root).with_suffix("")) for path in sorted(fuzzy)
            )
            raise AmbiguousEntryError(
                f"ambiguous: {len(fuzzy)} matches for {token!r}:\n  {listing}"
            )

        for entry in self.iter_entries(meta_only=True):
            url = entry.meta.get("url", "")
            if url and rid(url) == token:
                return entry.path
        return None
