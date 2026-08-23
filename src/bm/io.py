"""Input/Output functions for bookmarks."""

import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .errors import ConflictError
from .models import FM_END, FM_START

# Compatibility alias for callers that imported the old persistence exception.
ConcurrentModificationError = ConflictError


def _normalize_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Map legacy keys and ensure shapes."""
    m = dict(meta)
    # legacy -> canonical
    if "added" in m and "created" not in m:
        m["created"] = m.pop("added")
    if "updated" in m and "modified" not in m:
        m["modified"] = m.pop("updated")
    # shapes
    if "tags" in m and isinstance(m["tags"], str):
        m["tags"] = [t.strip() for t in m["tags"].split(",") if t.strip()]
    if "tags" not in m:
        m["tags"] = []
    return m


def _parse_tags(v: str) -> List[str]:
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        if inner:
            parts = []
            buf, inq = "", False
            for ch in inner:
                if ch in "\"'":
                    inq = not inq
                    continue
                if ch == "," and not inq:
                    if buf.strip():
                        parts.append(buf.strip())
                    buf = ""
                else:
                    buf += ch
            if buf.strip():
                parts.append(buf.strip())
            return [t.strip() for t in parts if t.strip()]
        else:
            return []
    else:
        return [t.strip() for t in v.split(",") if t.strip()]


def _parse_no_front_matter(text: str) -> Tuple[Dict[str, Any], str]:
    lines = text.splitlines()
    meta = {}
    body = text
    if lines:
        maybe_url = lines[0].strip()
        if maybe_url.startswith("http://") or maybe_url.startswith("https://"):
            meta["url"] = maybe_url
            body = "\n".join(lines[1:]).lstrip("\n")
    return _normalize_meta(meta), body


def _consume_block_scalar(lines: List[str], start: int) -> Tuple[str, int]:
    """Collect a `|` block scalar starting at `start`. Returns (value, next_i).

    Trailing blank lines are dropped; interior blanks (truly empty or
    whitespace-only) are preserved when the block continues.
    """
    block: List[str] = []
    pending_blanks = 0
    block_indent = None
    i = start
    while i < len(lines):
        cont_raw = lines[i]
        stripped_text = cont_raw.lstrip()
        if not stripped_text:
            pending_blanks += 1
            i += 1
            continue
        indent_len = len(cont_raw) - len(stripped_text)
        if block_indent is None:
            if indent_len == 0:
                break
            block_indent = indent_len
        if indent_len < block_indent:
            break
        block.extend([""] * pending_blanks)
        pending_blanks = 0
        block.append(cont_raw[block_indent:])
        i += 1
    return "\n".join(block), i


def _parse_header(header: str) -> Dict[str, Any]:
    meta: Dict[str, Any] = {}
    lines = header.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.strip()
        if not line or line.startswith("#"):
            i += 1
            continue

        if ":" not in line:
            i += 1
            continue

        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()

        if value == "|":
            meta[key], i = _consume_block_scalar(lines, i + 1)
            continue

        if key == "tags":
            meta["tags"] = _parse_tags(value)
        else:
            meta[key] = value

        i += 1

    return meta


def _split_front_matter(text: str) -> Optional[Tuple[str, str]]:
    """Return header/body when text has complete line-delimited front matter."""
    if text.startswith("---\r\n"):
        opening_end = 5
    elif text.startswith(FM_START):
        opening_end = len(FM_START)
    else:
        return None

    rest = text[opening_end:]
    offset = 0
    for line in rest.splitlines(keepends=True):
        if line.rstrip("\r\n") == "---":
            end = offset + len(line)
            return rest[:offset], rest[end:]
        offset += len(line)
    return None


def parse_front_matter(text: str) -> Tuple[Dict[str, Any], str]:
    """Parse front matter whose opening and closing markers occupy full lines."""
    parts = _split_front_matter(text)
    if parts is None:
        if text.startswith(FM_START) or text.startswith("---\r\n"):
            return {"tags": []}, text
        return _parse_no_front_matter(text)

    header, body = parts
    meta = _parse_header(header)
    return _normalize_meta(meta), body.lstrip("\r\n")


def _fmt_tag(t: str) -> str:
    """Quote tags containing commas, spaces, or empty."""
    return f'"{t}"' if ("," in t or " " in t or t == "") else t


def build_text(meta: Dict[str, Any], body: str) -> str:
    """Render front matter with ordered keys; lists as [a, b] with quoting when needed."""
    m = _normalize_meta(meta)
    m = {k: v for k, v in m.items() if v not in (None, "", [])}
    order = ["url", "title", "tags", "created", "modified", "notes"]
    keys = [k for k in order if k in m] + [k for k in m if k not in order]
    lines = [FM_START]
    for k in keys:
        v = m[k]
        if isinstance(v, list):
            lines.append(f"{k}: [{', '.join(_fmt_tag(t) for t in v)}]\n")
        else:
            if "\n" in str(v):
                lines.append(f"{k}: |\n")
                for ln in str(v).splitlines():
                    lines.append(f"  {ln}\n")
            else:
                lines.append(f"{k}: {v}\n")
    lines.append(FM_END)
    fm = "".join(lines)
    return fm + (body or "")


def _read_meta_only(fpath: Path) -> str:
    """Read through the closing front-matter line without reading the body."""
    with open(fpath, "rb") as f:
        first = f.readline()
        if first.rstrip(b"\r\n") != b"---":
            return (first + f.read()).decode("utf-8", errors="replace")

        lines = [first]
        for line in f:
            lines.append(line)
            if line.rstrip(b"\r\n") == b"---":
                break
        return b"".join(lines).decode("utf-8", errors="replace")


def load_entry(fpath: Path, meta_only: bool = False) -> Tuple[Dict[str, Any], str]:
    """Load meta and body from file. If meta_only, skip body parsing."""
    if meta_only:
        text = _read_meta_only(fpath)
        meta, _ = parse_front_matter(text)
        return meta, ""
    text = fpath.read_text(encoding="utf-8", errors="replace")
    meta, body = parse_front_matter(text)
    return meta, body


def _refuse_symlink(path: Path) -> None:
    """Reject a symlink destination before replacing it."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(st.st_mode):
        raise OSError(f"refusing to overwrite symlink: {path}")


def _check_expected(path: Path, expected: bytes) -> None:
    """Raise if ``path`` no longer contains the bytes that were read."""
    try:
        current = path.read_bytes()
    except FileNotFoundError as exc:
        raise ConflictError(f"bookmark changed since it was read: {path}") from exc
    if current != expected:
        raise ConflictError(f"bookmark changed since it was read: {path}")


def _fsync_directory(path: Path) -> None:
    """Best-effort fsync of a containing directory where the platform allows it."""
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        directory_fd = os.open(path, flags)
    except OSError:
        return
    try:
        try:
            os.fsync(directory_fd)
        except OSError:
            # Windows and some filesystems do not support syncing directories.
            pass
    finally:
        os.close(directory_fd)


def _write_temp(fd: int, data: str) -> None:
    """Write and fsync a temporary text file before publishing it."""
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def atomic_write(path: Path, data: str, expected: Optional[bytes] = None) -> None:
    """Write data to path atomically and, when requested, only if unchanged.

    Refuses to overwrite an existing symlink at ``path`` so a planted symlink
    cannot redirect the write outside the store. ``expected`` is the byte
    snapshot read by a caller performing a read/modify/write operation; a
    mismatch raises :class:`ConflictError` and leaves the existing file
    untouched.
    """
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        _refuse_symlink(path)
        if expected is not None:
            _check_expected(path, expected)
        _write_temp(fd, data)
        # Recheck the symlink policy before the final optimistic-content check.
        # A portable filesystem compare-and-swap is unavailable, so an external
        # writer can still race the check and replace; callers get conflict
        # detection for every change observable before this final check.
        _refuse_symlink(path)
        if expected is not None:
            _check_expected(path, expected)
        os.replace(tmp_name, path)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_create(path: Path, data: str) -> None:
    """Create a file without replacing a destination that appears concurrently.

    Data is written and fsynced to a same-directory temporary file first. A
    hard link publishes that complete file atomically and fails with
    ``FileExistsError`` when another writer wins. Filesystems without hard-link
    support fail explicitly; this function never falls back to replacement. A
    crash after publication can leave the temporary hard-link name behind, but
    the destination remains complete and no existing file is overwritten.
    """
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        _refuse_symlink(path)
        _write_temp(fd, data)
        _refuse_symlink(path)
        os.link(tmp_name, path)
        _fsync_directory(path.parent)
        os.unlink(tmp_name)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_move_no_replace(source: Path, destination: Path) -> None:
    """Move a file without replacing a destination, using link-then-unlink.

    The destination link is created atomically and fails if it already exists.
    A crash after linking but before unlinking the source can leave both names;
    it cannot destroy or overwrite either file. Unsupported hard links fail
    explicitly rather than falling back to replacing rename semantics.
    """
    _refuse_symlink(source)
    _refuse_symlink(destination)
    os.link(source, destination)
    _fsync_directory(destination.parent)
    os.unlink(source)
    _fsync_directory(source.parent)
