"""Input/Output functions for bookmarks."""

import os
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


def _parse_header(header: str) -> Dict[str, Any]:
    meta = {}
    for raw in header.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):  # allow comments
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            k = k.strip().lower()
            v = v.strip()
            if k == "tags":
                meta["tags"] = _parse_tags(v)
            else:
                meta[k] = v
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


def load_entry(fpath: Path) -> Tuple[Dict[str, Any], str]:
    """Load meta and body from file."""
    text = fpath.read_text(encoding="utf-8", errors="replace")
    meta, body = parse_front_matter(text)
    return meta, body


def atomic_write(path: Path, data: str) -> None:
    """Write data to path atomically."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)
