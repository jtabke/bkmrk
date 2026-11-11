"""Input/Output functions for bookmarks."""

import json
import os
import re
import stat
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from io import TextIOWrapper
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .errors import ConflictError
from .models import FM_END, FM_START, Metadata

# Compatibility alias for callers that imported the old persistence exception.
ConcurrentModificationError = ConflictError

# Match the same line starts as str.splitlines(), without splitting the body.
# Only CR/LF (or EOF) may terminate a delimiter, matching the old rstrip check.
_FRONT_MATTER_CLOSE = re.compile(
    r"(?:\A|(?<=[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]))---(?:\r\n|[\r\n]|\Z)"
)


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


def _parse_tags(value: str, diagnostics: Optional[List[str]] = None) -> List[str]:
    """Read literal legacy tags or an explicitly marked JSON string array."""
    if value.startswith("json:["):
        return _parse_json_tags(value, diagnostics)
    if not (value.startswith("[") and value.endswith("]")):
        return [tag.strip() for tag in value.split(",") if tag.strip()]
    parts = []
    buffer: List[str] = []
    quote = ""
    for char in value[1:-1]:
        if quote:
            buffer.append(char)
            if char == quote:
                quote = ""
        elif char in "\"'" and not "".join(buffer).strip():
            quote = char
            buffer.append(char)
        elif char == ",":
            parts.append("".join(buffer).strip())
            buffer = []
        else:
            buffer.append(char)
    parts.append("".join(buffer).strip())
    return [_unquote_tag(part) for part in parts if part]


def _parse_json_tags(value: str, diagnostics: Optional[List[str]]) -> List[str]:
    """Decode the explicit escaped representation, retaining damaged text for recovery."""
    try:
        tags = json.loads(value[5:])
    except ValueError:
        tags = None
    if isinstance(tags, list) and all(isinstance(tag, str) for tag in tags):
        return tags
    if diagnostics is not None:
        diagnostics.append("Invalid JSON-encoded tags (expected an array of strings).")
    # Retain damaged encoded text rather than discarding it during a rewrite.
    return [value]


def _unquote_tag(value: str) -> str:
    """Remove legacy enclosing quotes without interpreting any backslashes."""
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        return value[1:-1]
    return value


def _parse_no_front_matter(text: str) -> Tuple[Metadata, str]:
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


def _parse_header(header: str, diagnostics: Optional[List[str]] = None) -> Metadata:
    meta: Metadata = {}
    lines = header.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.strip()
        if not line or line.startswith("#"):
            i += 1
            continue

        key, separator, value = line.partition(":")
        key = key.strip().lower()
        if not separator or not key:
            if diagnostics is not None:
                diagnostics.append(f"Invalid front-matter line: {line!r}.")
            if not separator:
                i += 1
                continue
        if key in meta and diagnostics is not None:
            diagnostics.append(f"Repeated front-matter key: {key!r}.")
        value = value.strip()

        if value == "|":
            meta[key], i = _consume_block_scalar(lines, i + 1)
            continue

        if key == "tags":
            meta["tags"] = _parse_tags(value, diagnostics)
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

    closing = _FRONT_MATTER_CLOSE.search(text, opening_end)
    if closing is None:
        return None
    return text[opening_end : closing.start()], text[closing.end() :]


def parse_front_matter(
    text: str, *, diagnostics: Optional[List[str]] = None
) -> Tuple[Metadata, str]:
    """Parse front matter whose opening and closing markers occupy full lines."""
    parts = _split_front_matter(text)
    if parts is None:
        if diagnostics is not None:
            diagnostics.append(
                "Missing or incomplete front matter (expected opening and closing --- lines)."
            )
        if text.startswith(FM_START) or text.startswith("---\r\n"):
            return {"tags": []}, text
        return _parse_no_front_matter(text)

    header, body = parts
    meta = _parse_header(header, diagnostics)
    return _normalize_meta(meta), body.lstrip("\r\n")


def _fmt_tag(t: str) -> str:
    """Quote special tags; the list renderer marks any necessary JSON escaping."""
    if not t or any(char.isspace() or char in ",\\\"'" for char in t):
        # ASCII escapes keep Unicode line separators inside one header line.
        return json.dumps(t)
    return t


def _format_tags(tags: List[str]) -> str:
    """Keep ordinary lists readable; mark escaped strings to avoid legacy ambiguity."""
    parts = [_fmt_tag(tag) for tag in tags]
    if any("\\" in part for part in parts):
        return "json:" + json.dumps(tags)
    return "[" + ", ".join(parts) + "]"


def build_text(meta: Dict[str, Any], body: str) -> str:
    """Render front matter with ordered keys; lists as [a, b] with quoting when needed."""
    m = _normalize_meta(meta)
    m = {k: v for k, v in m.items() if v not in (None, "", [])}
    order = ["url", "title", "tags", "created", "modified", "notes"]
    keys = [k for k in order if k in m] + [k for k in m if k not in order]
    lines = [FM_START]
    for k in keys:
        v = m[k]
        if k == "tags":
            lines.append(f"{k}: {_format_tags(v)}\n")
        elif isinstance(v, list):
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


def load_entry(fpath: Path, meta_only: bool = False) -> Tuple[Metadata, str]:
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


def verify_snapshot(path: Path, expected: bytes) -> None:
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


def _write_temp(fd: int, data: str | bytes) -> None:
    """Write and fsync a temporary file before publishing it."""
    if isinstance(data, bytes):
        with os.fdopen(fd, "wb") as binary_stream:
            binary_stream.write(data)
            binary_stream.flush()
            os.fsync(binary_stream.fileno())
    else:
        with os.fdopen(fd, "w", encoding="utf-8") as text_stream:
            text_stream.write(data)
            text_stream.flush()
            os.fsync(text_stream.fileno())


def atomic_write(path: Path, data: str | bytes, expected: Optional[bytes] = None) -> None:
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
            verify_snapshot(path, expected)
        _write_temp(fd, data)
        # Recheck the symlink policy before the final optimistic-content check.
        # A portable filesystem compare-and-swap is unavailable, so an external
        # writer can still race the check and replace; callers get conflict
        # detection for every change observable before this final check.
        _refuse_symlink(path)
        if expected is not None:
            verify_snapshot(path, expected)
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


@contextmanager
def atomic_text_create(path: Path) -> Generator[TextIOWrapper, None, None]:
    """Stream UTF-8 to a temporary file, then publish complete content without clobbering.

    A failed serializer or publication leaves no partial destination. Hard-link
    publication is mandatory; unsupported filesystems never fall back to replacement.
    """
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            _refuse_symlink(path)
            yield stream
            stream.flush()
            os.fsync(stream.fileno())
        _refuse_symlink(path)
        os.link(tmp, path)
        _fsync_directory(path.parent)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        _fsync_directory(path.parent)


def atomic_create(path: Path, data: str | bytes) -> None:
    """Publish complete content without replacing an existing destination."""
    # The concrete UTF-8 wrapper also exposes its binary buffer so exact-byte
    # callers share publication/cleanup without decoding or newline translation.
    with atomic_text_create(path) as stream:
        if isinstance(data, bytes):
            stream.buffer.write(data)
        else:
            stream.write(data)


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
