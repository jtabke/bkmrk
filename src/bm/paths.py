"""Bookmark path syntax, filename rules, and literal path-prefix matching."""

import ntpath
import re
import sys
from pathlib import Path
from typing import Optional

from .errors import UnsafePathError
from .models import FILE_EXT


def normalize_slug(s: str) -> str:
    """Normalize string to a slug."""
    s = s.lower().strip().strip("/").replace(" ", "-")
    s = re.sub(r"[^\w\-/\.]", "", s)
    s = re.sub(r"-{2,}", "-", s)
    s = s.strip("-")
    if "/" in s:
        parts = [p.strip("-") for p in s.split("/") if p]
        s = "/".join(parts)
    return s or "untitled"


def reject_absolute_path(rel: str) -> None:
    """Reject POSIX and Windows absolute path syntax before normalization."""
    if rel.startswith(("/", "\\")) or ntpath.isabs(rel):
        raise UnsafePathError("absolute paths not allowed")
    if ntpath.splitdrive(rel)[0]:
        raise UnsafePathError("drive-qualified paths not allowed")


def reject_unsafe(rel: str) -> str:
    """Reject unsafe path segments.

    Rejects: absolute paths, any all-dots segment ('..', '...', etc.), and
    NUL bytes. Leading-dot segments like '.git' are accepted (caller may need
    them); all-dots are blocked because they enable parent traversal or
    upload to ambiguous filesystem locations.
    """
    if "\x00" in rel:
        raise UnsafePathError("null byte in path not allowed")
    reject_absolute_path(rel)
    rel = rel.replace("\\", "/")
    parts = [p for p in rel.split("/") if p]
    for p in parts:
        if p and set(p) == {"."}:
            raise UnsafePathError(f"unsafe path segment {p!r} not allowed")
    return "/".join(parts)


def reject_windows_names(rel: str) -> None:
    """Reject Windows device names and unsupported filesystem components."""
    for part in rel.replace("\\", "/").split("/"):
        if part.endswith((".", " ")):
            raise UnsafePathError(f"unsupported Windows filename {part!r}: trailing dot or space")
        if any(ord(char) < 32 or char in '<>:"|?*' for char in part):
            raise UnsafePathError(f"unsupported Windows filename {part!r}: invalid character")
        stem = part.split(".", 1)[0].rstrip(" ").upper()
        if stem in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} or re.fullmatch(
            r"(?:COM|LPT)[1-9¹²³]", stem
        ):
            raise UnsafePathError(f"unsupported Windows filename {part!r}: reserved device name")


def is_relative_to(path: Path, base: Path) -> bool:
    """Check if path is relative to base."""
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except Exception:
        return False


def id_to_path(store: Path, slug: str) -> Path:
    """Convert slug to a store-relative path; rejects escapes."""
    # Validate the raw input first: normalize_slug intentionally strips
    # leading slashes for URL-derived slugs, but user paths must not be
    # silently reinterpreted as relative paths.
    slug = normalize_slug(reject_unsafe(slug))
    slug = reject_unsafe(slug)
    reject_reserved_namespace(slug + FILE_EXT)
    if sys.platform == "win32":
        reject_windows_names(slug + FILE_EXT)
    fpath = store / (slug + FILE_EXT)
    if not is_relative_to(fpath, store):
        raise UnsafePathError("destination escapes store")
    return fpath


def normalize_path_prefix(value: Optional[str]) -> str:
    """Normalize separators and remove surrounding slashes from a path filter."""
    return value.replace("\\", "/").strip("/") if value else ""


def matches_path_prefix(rel: Path, path_prefix: str) -> bool:
    path_prefix = normalize_path_prefix(path_prefix)
    if not path_prefix:
        return True
    logical_path = rel.as_posix()
    return logical_path.startswith(path_prefix + "/") or logical_path == path_prefix


def is_reserved_path(name: str) -> bool:
    """Return whether a relative bookmark path includes a Git metadata namespace."""
    return any(part.casefold() == ".git" for part in name.replace("\\", "/").split("/"))


def reject_reserved_namespace(name: str) -> None:
    """Keep bookmark paths out of Git metadata, including nested repositories."""
    if is_reserved_path(name):
        raise UnsafePathError(f"reserved bookmark namespace: {name!r}")
