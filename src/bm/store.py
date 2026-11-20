"""Concrete filesystem boundary for bookmark stores."""

import os
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
    is_reserved_path,
    matches_path_prefix,
    normalize_path_prefix,
    normalize_slug,
    reject_reserved_namespace,
    reject_unsafe,
    validate_bookmark_name,
)
from .utils import rid


@dataclass(slots=True)
class _ScanPath:
    """A discovered name and its ancestors below the root, not proof of membership."""

    path: Path
    relative: Path
    parents: tuple[Path, ...]


def _plain_directory(path: Path) -> bool:
    info = path.lstat()
    # Windows' name-surrogate bit includes junctions, but not cloud placeholders
    # that simply supply a directory's contents. POSIX symlinks fail S_ISDIR.
    redirects = getattr(info, "st_reparse_tag", 0) & 0x20000000
    return stat.S_ISDIR(info.st_mode) and not redirects


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
            if current.is_junction():
                raise UnsafePathError(f"junction in bookmark destination: {current}")
            if not current.exists():
                continue
            if current == checked:
                if not current.is_file():
                    raise UnsafePathError(f"destination is not a regular file: {current}")
            elif not current.is_dir():
                raise UnsafePathError(f"parent is not a directory: {current}")

    def require_entry(self, token: str, *, exact_path: bool = False) -> Path:
        """Resolve a bookmark or report the shared actionable not-found diagnostic."""
        path = self.resolve(token, exact_path=exact_path)
        if path is None:
            if exact_path:
                raise NotFoundError(
                    f"bookmark path not found: {token!r}; check the path in 'bm list'"
                )
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
        return self._load_checked(
            checked,
            bookmark_token(checked.relative_to(self.root)),
            meta_only=False,
            snapshot=snapshot,
        )

    def _load_checked(
        self, checked: Path, token: Path, *, meta_only: bool, snapshot: bool
    ) -> StoreEntry:
        raw_snapshot = None
        if snapshot:
            raw_snapshot = checked.read_bytes()
            meta, body = parse_for_rewrite(raw_snapshot, checked)
        else:
            meta, body = load_entry(checked, meta_only=meta_only)
        return StoreEntry(
            path=checked,
            relative_path=token,
            meta=meta,
            body=body,
            snapshot=raw_snapshot,
        )

    def _walk(self, start: _ScanPath) -> Iterator[_ScanPath]:
        """Scan each directory once, retaining relative names and parent chains."""
        pending = [start]
        while pending:
            directory = pending.pop()
            parents = directory.parents
            if directory.relative.parts:
                parents = (*parents, directory.path)
            try:
                # Check the start of a prefix scan as well as discovered directories.
                if is_reserved_path(directory.relative.as_posix()) or not all(
                    _plain_directory(parent) for parent in parents
                ):
                    continue
                with os.scandir(directory.path) as items:
                    for item in items:
                        try:
                            child_dir = item.is_dir(follow_symlinks=False)
                        except OSError:
                            child_dir = False
                        bookmark = os.path.normcase(item.name).endswith(FILE_EXT)
                        if not (child_dir or bookmark):
                            continue
                        child = _ScanPath(
                            directory.path / item.name, directory.relative / item.name, parents
                        )
                        if bookmark:
                            yield child
                        if child_dir:
                            pending.append(child)
            except OSError:
                # Ordinary scans are best effort. Backup and doctor have stricter walkers.
                continue

    def _iter_paths(self, prefix: str) -> Iterator[_ScanPath]:
        start = _ScanPath(self.root, Path(), ())
        if not prefix:
            yield from self._walk(start)
            return
        try:
            safe = reject_unsafe(prefix)
            validate_bookmark_name(bookmark_filename(prefix))
        except UnsafePathError:
            safe = ""
        if safe != prefix:
            # Preserve literal string matching for unusual prefixes.
            yield from self._walk(start)
            return

        folder = Path(prefix)
        parents = tuple(self.root / p for p in reversed(folder.parents) if p.parts)
        relative = Path(bookmark_filename(prefix))
        exact = self.root / relative
        try:
            if not all(_plain_directory(parent) for parent in parents):
                return
            if exact.exists() or exact.is_symlink():
                yield _ScanPath(exact, relative, parents)
        except OSError:
            return
        yield from self._walk(_ScanPath(self.root / folder, folder, parents))

    def _checked_paths(
        self, paths: Iterable[_ScanPath], on_error: Callable[[Path, Exception], None] | None = None
    ) -> Iterator[tuple[Path, Path]]:
        for item in paths:
            token = bookmark_token(item.relative)
            try:
                validate_bookmark_name(item.relative.as_posix())
                # Names come from the walk, but the filesystem can change between yields.
                # Live lstat of each component below the selected root proves the simple
                # no-links case without resolving the entire root twice per bookmark.
                if not all(_plain_directory(parent) for parent in item.parents):
                    raise UnsafePathError("linked or replaced bookmark directory")
                info = item.path.lstat()
                # Windows may resolve short names or other filesystem aliases even
                # without a symlink. Keep full resolution there and for linked files.
                if os.name == "nt" or stat.S_ISLNK(info.st_mode):
                    self._require_file(self._check_member(item.path))
                elif not stat.S_ISREG(info.st_mode):
                    raise UnsafePathError(f"not a regular bookmark file: {item.path}")
            except (OSError, UnsafePathError, ValueError) as exc:
                if on_error is not None:
                    on_error(token, exc)
                continue
            yield item.path, token

    def iter_named_paths(
        self, *, path_prefix: str = "", on_error: Callable[[Path, Exception], None] | None = None
    ) -> Iterator[tuple[Path, Path]]:
        """Yield live-checked file paths together with their literal lookup tokens."""
        prefix = normalize_path_prefix(path_prefix)
        paths = self._iter_paths(prefix)
        if prefix:
            paths = (p for p in paths if matches_path_prefix(bookmark_token(p.relative), prefix))
        yield from self._checked_paths(paths, on_error)

    def iter_paths(
        self, *, path_prefix: str = "", on_error: Callable[[Path, Exception], None] | None = None
    ) -> Iterator[Path]:
        """Yield checked regular bookmark paths without opening them."""
        for path, _ in self.iter_named_paths(path_prefix=path_prefix, on_error=on_error):
            yield path

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
        for path, token in self.iter_named_paths(path_prefix=path_prefix, on_error=on_error):
            try:
                entry = self._load_checked(path, token, meta_only=meta_only, snapshot=snapshot)
            except (OSError, BmError, ValueError) as exc:
                if on_error is not None:
                    on_error(token, exc)
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

    def _id_matches(self, paths: Iterable[_ScanPath], token: str) -> list[Path]:
        matches = []
        # Validate each buffered name at read time, not just at discovery time.
        for path, _ in self._checked_paths(paths):
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

    def resolve(self, token: str, *, exact_path: bool = False) -> Optional[Path]:
        """Try the listed path; only ordinary lookup falls back to slug, partial name, or ID."""
        logical = reject_unsafe(token)
        if exact_path and logical != token.replace(os.sep, "/"):
            raise UnsafePathError(f"expected a path as printed by 'bm list': {token!r}")
        literal = bookmark_filename(logical) if logical else ""
        if literal and (exact := self._existing_file(literal)):
            if exact_path:
                self.check_destination(exact)
            return exact
        if exact_path:
            return None

        token = token.strip()
        slug = reject_unsafe(normalize_slug(reject_unsafe(token)))
        if slug + FILE_EXT != literal and (exact := self._existing_file(slug + FILE_EXT)):
            return exact

        name = Path(slug).name
        is_id = re.fullmatch(r"[0-9a-f]{12}", token) is not None
        paths: Iterable[_ScanPath] = self._iter_paths("")
        if is_id:
            # Only ID-shaped tokens might need headers. Walk once and preserve
            # partial-name priority without opening anything if a name matches.
            paths = list(paths)
        names = (item for item in paths if name in item.path.stem)
        matches = [path for path, _ in self._checked_paths(names)]
        if is_id and not matches:
            matches = self._id_matches(paths, token)
        return self._single_match(matches, token)
