"""Utility functions for the bookmark manager."""

import hashlib
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from .models import FILE_EXT


def die(msg: str, code: int = 1) -> None:
    """Print an error message to stderr and exit with the given code.

    Args:
        msg: The error message to print.
        code: The exit code (default 1).
    """
    print(f"bm: {msg}", file=sys.stderr)
    sys.exit(code)


def iso_now() -> str:
    """Return current ISO-8601 timestamp with local offset, no microseconds."""
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _normalize_iso_z(ts: str) -> str:
    """Accept trailing Z and convert to +00:00 for fromisoformat."""
    return ts[:-1] + "+00:00" if ts and ts.endswith("Z") else ts


def parse_iso(ts: str) -> Optional[datetime]:
    """Parse an ISO-like timestamp string into a datetime object.

    Accepts 'YYYY-MM-DD' (treated as start-of-day local time) and full ISO formats.
    Returns an aware datetime or None if parsing fails.

    Args:
        ts: The timestamp string to parse.

    Returns:
        A timezone-aware datetime object, or None if invalid.
    """
    if not ts:
        return None
    ts = ts.strip()
    try:
        # bare date → treat as start-of-day local time
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", ts):
            dt = datetime.fromisoformat(ts + "T00:00:00")
            return dt.astimezone()  # localize
        return datetime.fromisoformat(_normalize_iso_z(ts))
    except Exception:
        return None


def to_epoch(dt: Optional[datetime]) -> Optional[int]:
    """Convert datetime to epoch timestamp."""
    if not dt:
        return None
    return int(dt.timestamp())


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


def _reject_unsafe(rel: str) -> str:
    """Reject unsafe path segments."""
    parts = [p for p in rel.split("/") if p]
    if any(p == ".." for p in parts):
        die("unsafe path segment '..' not allowed")
    if rel.startswith("/"):
        die("absolute paths not allowed")
    return "/".join(parts)


def is_relative_to(path: Path, base: Path) -> bool:
    """Check if path is relative to base."""
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except Exception:
        return False


def id_to_path(store: Path, slug: str) -> Path:
    """Convert slug to path."""
    slug = normalize_slug(slug)
    slug = _reject_unsafe(slug)
    return store / (slug + FILE_EXT)


def _short_sha(s: str) -> str:
    """Short SHA1 hash."""
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:7]


def create_slug_from_url(url: str) -> str:
    """Derive human-readable slug + short hash (collision-resistant)."""
    try:
        p = urlparse(url)
        host = (p.netloc or "link").lower().replace("www.", "")
        host = host.replace(":", "-").replace(".", "-")
        last = p.path.strip("/").split("/")[-1] if p.path and p.path != "/" else ""
        base = f"{host}/{last}" if last else host
        base = normalize_slug(base)
    except Exception:
        base = normalize_slug(url.replace("://", "_").replace("/", "-"))
    return f"{base}-{_short_sha(url)}"


def rid(url: str) -> str:
    """Stable short ID based on URL only (rename-safe)."""
    return hashlib.blake2b(url.encode("utf-8"), digest_size=6).hexdigest()


def _launch_editor(path: Path) -> None:
    """Launch editor for the given path."""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if editor:
        cmd = shlex.split(editor) + [str(path)]
    else:
        cmd = ["notepad", str(path)] if os.name == "nt" else ["vi", str(path)]
    subprocess.call(cmd, shell=False)
