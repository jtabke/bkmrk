"""Input/Output functions for bookmarks."""

import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .models import FM_END, FM_START


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


def parse_front_matter(text: str) -> Tuple[Dict[str, Any], str]:
    """
    Simple front matter parser:
    ---\n
    key: value
    ...
    ---\n
    <body>
    Supports:
      - tags: [a, b, "needs,comma"] or "a, b"
      - added/updated (legacy) -> normalized to created/modified
    """
    if not text.startswith(FM_START):
        return _parse_no_front_matter(text)

    rest = text[len(FM_START) :]
    end_idx = rest.find(FM_END)
    if end_idx == -1:
        return _normalize_meta({}), text

    header = rest[:end_idx]
    body = rest[end_idx + len(FM_END) :]
    meta = _parse_header(header)
    return _normalize_meta(meta), body.lstrip("\n")


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


_FM_DELIM = b"---\n"


def _read_meta_only(fpath: Path) -> str:
    """Read just enough bytes to locate the closing FM_END marker.

    Falls back to the full file when the second `---` cannot be found
    in the prefix (so callers see the same behavior as the prior path).
    """
    chunk_size = 8192
    buf = bytearray()
    with open(fpath, "rb") as f:
        while True:
            piece = f.read(chunk_size)
            if not piece:
                break
            buf.extend(piece)
            if buf.startswith(_FM_DELIM):
                # Locate the closing delimiter after the opening one.
                close = buf.find(_FM_DELIM, len(_FM_DELIM))
                if close != -1:
                    end = close + len(_FM_DELIM)
                    return buf[:end].decode("utf-8", errors="replace")
            elif len(buf) >= len(_FM_DELIM):
                # Not a front-matter file; let the regular parser handle it.
                return buf.decode("utf-8", errors="replace") + f.read().decode(
                    "utf-8", errors="replace"
                )
    return buf.decode("utf-8", errors="replace")


def load_entry(fpath: Path, meta_only: bool = False) -> Tuple[Dict[str, Any], str]:
    """Load meta and body from file. If meta_only, skip body parsing."""
    if meta_only:
        text = _read_meta_only(fpath)
        meta, _ = parse_front_matter(text)
        return meta, ""
    text = fpath.read_text(encoding="utf-8", errors="replace")
    meta, body = parse_front_matter(text)
    return meta, body


def atomic_write(path: Path, data: str) -> None:
    """Write data to path atomically.

    Refuses to overwrite an existing symlink at `path` so a planted symlink
    cannot redirect the write outside the store. (Caller's `id_to_path`
    verifies parent-path containment; this guards the final dest.)
    """
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            pass
        else:
            if stat.S_ISLNK(st.st_mode):
                raise OSError(f"refusing to overwrite symlink: {path}")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
