"""Utility functions for the bookmark manager."""

import hashlib
import os
import posixpath
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import ParseResult, parse_qsl, urlencode, urlparse

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


def _normalize_netloc_for_compare(scheme: str, netloc: str) -> str:
    """Normalize host/userinfo/port for canonical URL comparison."""
    if not netloc:
        return ""
    netloc = netloc.strip()
    # Split userinfo from host:port if present
    if "@" in netloc:
        userinfo, host_port = netloc.rsplit("@", 1)
        userinfo = userinfo.lower()
    else:
        userinfo, host_port = "", netloc

    host_port = host_port.lower()
    if host_port.startswith("www."):
        host_port = host_port[4:]

    if ":" in host_port:
        host, port_str = host_port.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            host = host_port  # fallback; keep raw if port invalid
            port = None
        else:
            default_http = scheme in {"http", ""} and port == 80
            default_https = scheme == "https" and port == 443
            if default_http or default_https:
                port = None
        host_port = host if port is None else f"{host}:{port}"

    netloc_norm = host_port
    if userinfo:
        netloc_norm = f"{userinfo}@{netloc_norm}"
    return netloc_norm


def _normalize_path_for_compare(path: str) -> str:
    """Normalize URL path for comparison (collapse slashes, remove trailing '/')."""
    if not path:
        return ""
    # Ensure leading slash when path exists (unless already has it like //resource)
    if not path.startswith("/"):
        path = f"/{path}"

    # Collapse duplicate slashes and resolve ".."/"." segments
    collapsed = re.sub(r"/+", "/", path)
    normalized = posixpath.normpath(collapsed)

    # posixpath.normpath strips trailing slash; treat root specially
    if normalized == ".":
        normalized = ""
    elif normalized == "/":
        normalized = ""

    return normalized


def _parse_for_compare(raw: str) -> Tuple[ParseResult, str]:
    """Return a parsed URL and canonical scheme for comparison."""
    parsed = urlparse(raw)
    if parsed.scheme or parsed.netloc or "://" in raw:
        return parsed, (parsed.scheme or "").lower()

    candidate = urlparse(f"http://{raw}")
    if candidate.netloc:
        return candidate, "http"
    return parsed, (parsed.scheme or "").lower()


def _normalize_query_string(query: str) -> str:
    """Sort query parameters and rebuild a stable query string."""
    if not query:
        return ""
    pairs = parse_qsl(query, keep_blank_values=True)
    if not pairs:
        return ""
    pairs.sort()
    return urlencode(pairs, doseq=True)


def _compose_web_key(scheme: str, netloc: str, path: str, query: str) -> str:
    prefix = "" if scheme in {"", "http", "https"} else f"{scheme}://"
    key = f"{prefix}{netloc}{path}"
    if query:
        key = f"{key}?{query}"
    return key


def _compose_non_web_key(parsed: ParseResult, scheme: str, query: str, raw: str) -> str:
    if scheme:
        suffix = parsed.path
        if parsed.params:
            suffix = f"{suffix};{parsed.params}" if suffix else f";{parsed.params}"
        if query:
            suffix = f"{suffix}?{query}" if suffix else f"?{query}"
        return f"{scheme}:{suffix}"
    return raw.lower()


def normalize_url_for_compare(url: str) -> str:
    """Return a normalized key suitable for grouping equivalent URLs.

    The normalization aims to treat typical duplicates as equal while keeping
    non-web schemes distinct. Rules:
      • Lowercase scheme and host, strip leading "www.".
      • Remove default ports (80 for HTTP, 443 for HTTPS).
      • Collapse redundant slashes and dot segments in the path; ignore trailing "/".
      • Drop fragments and params; sort query parameters (preserving duplicates).
      • Ignore scheme differences between HTTP and HTTPS (they map to the same key).
      • Inputs without an explicit scheme fall back to HTTP semantics.

    Args:
        url: Raw URL string from a bookmark entry.

    Returns:
        A normalized string used as the dedupe key. Empty string for unusable URLs.
    """

    if not url:
        return ""

    raw = url.strip()
    if not raw:
        return ""

    parsed, scheme = _parse_for_compare(raw)
    netloc = _normalize_netloc_for_compare(scheme, parsed.netloc)
    query = _normalize_query_string(parsed.query)

    if netloc:
        path = _normalize_path_for_compare(parsed.path)
        if parsed.params:
            path = f"{path};{parsed.params}" if path else f";{parsed.params}"
        return _compose_web_key(scheme, netloc, path, query)

    return _compose_non_web_key(parsed, scheme, query, raw)


def _launch_editor(path: Path) -> None:
    """Launch editor for the given path."""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if editor:
        cmd = shlex.split(editor) + [str(path)]
    else:
        cmd = ["notepad", str(path)] if os.name == "nt" else ["vi", str(path)]
    subprocess.call(cmd, shell=False)
