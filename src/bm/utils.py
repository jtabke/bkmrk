"""Utility functions for the bookmark manager."""

import hashlib
import ntpath
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import ParseResult, parse_qsl, urlencode, urlparse

from .errors import UnsafePathError
from .models import FILE_EXT


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
        dt = datetime.fromisoformat(_normalize_iso_z(ts))
        # Treat full timestamps without offsets as UTC, matching the existing
        # dedupe normalization policy while ensuring all results are aware.
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
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


def _reject_absolute_path(rel: str) -> None:
    """Reject POSIX and Windows absolute path syntax before normalization."""
    if rel.startswith(("/", "\\")) or ntpath.isabs(rel):
        raise UnsafePathError("absolute paths not allowed")
    if ntpath.splitdrive(rel)[0]:
        raise UnsafePathError("drive-qualified paths not allowed")


def _reject_unsafe(rel: str) -> str:
    """Reject unsafe path segments.

    Rejects: absolute paths, any all-dots segment ('..', '...', etc.), and
    NUL bytes. Leading-dot segments like '.git' are accepted (caller may need
    them); all-dots are blocked because they enable parent traversal or
    upload to ambiguous filesystem locations.
    """
    if "\x00" in rel:
        raise UnsafePathError("null byte in path not allowed")
    _reject_absolute_path(rel)
    rel = rel.replace("\\", "/")
    parts = [p for p in rel.split("/") if p]
    for p in parts:
        if p and set(p) == {"."}:
            raise UnsafePathError(f"unsafe path segment {p!r} not allowed")
    return "/".join(parts)


def _reject_windows_names(rel: str) -> None:
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
    slug = normalize_slug(_reject_unsafe(slug))
    slug = _reject_unsafe(slug)
    if sys.platform == "win32":
        _reject_windows_names(slug + FILE_EXT)
    fpath = store / (slug + FILE_EXT)
    if not is_relative_to(fpath, store):
        raise UnsafePathError("destination escapes store")
    return fpath


def _short_sha(s: str) -> str:
    """Short SHA1 hash."""
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:7]


def _strip_leading_www(host: str) -> str:
    """Strip only a leading www. label from a hostname."""
    return host[4:] if host.startswith("www.") else host


def _port_or_none(parsed: ParseResult) -> Optional[int]:
    """Return a parsed URL port, treating invalid ports as absent."""
    try:
        return parsed.port
    except ValueError:
        return None


def _is_default_web_port(scheme: str, port: Optional[int]) -> bool:
    """Return whether port is the default for HTTP/HTTPS URL comparison."""
    return (scheme in {"", "http"} and port == 80) or (scheme == "https" and port == 443)


def _looks_like_schemeless_host(netloc: str) -> bool:
    """Heuristic for deciding whether schemeless text starts with a host."""
    if not netloc:
        return False
    host = netloc.rsplit("@", 1)[-1].split(":", 1)[0].strip("[]").lower()
    return host == "localhost" or "." in host


def _parse_for_slug(raw: str) -> ParseResult:
    """Parse a URL for slug creation, recognizing conservative schemeless hosts."""
    parsed = urlparse(raw)
    if parsed.netloc or "://" in raw:
        return parsed
    if parsed.scheme and not _looks_like_schemeless_host(parsed.scheme):
        return parsed

    candidate = urlparse(f"http://{raw}")
    if _looks_like_schemeless_host(candidate.netloc):
        return candidate
    return parsed


def _slug_host(parsed: ParseResult) -> str:
    """Return a hostname-derived slug prefix, excluding userinfo and default ports."""
    host = (parsed.hostname or "link").lower()
    host = _strip_leading_www(host)
    host = host.replace(":", "-").replace(".", "-")

    scheme = (parsed.scheme or "").lower()
    port = _port_or_none(parsed)
    if port is not None and not _is_default_web_port(scheme, port):
        host = f"{host}-{port}"
    return host


def create_slug_from_url(url: str) -> str:
    """Derive human-readable slug + short hash (collision-resistant)."""
    try:
        p = _parse_for_slug(url.strip())
        host = _slug_host(p)
        last = p.path.strip("/").split("/")[-1] if p.path and p.path != "/" else ""
        base = f"{host}-{last}" if last else host
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
    host_port = _strip_leading_www(host_port)

    if ":" in host_port:
        host, port_str = host_port.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            host = host_port  # fallback; keep raw if port invalid
            port = None
        else:
            if _is_default_web_port(scheme, port):
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
    if parsed.netloc or "://" in raw:
        return parsed, (parsed.scheme or "").lower()
    if parsed.scheme and not _looks_like_schemeless_host(parsed.scheme):
        return parsed, parsed.scheme.lower()

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
      • Drop fragments; preserve params; sort query parameters (preserving duplicates).
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


def _split_editor_command(editor: str) -> List[str]:
    """Parse editor arguments using the host's command-line quoting rules."""
    if "\x00" in editor or not editor.strip():
        raise OSError("editor command is empty or contains a NUL byte")
    if sys.platform != "win32":
        try:
            return shlex.split(editor)
        except ValueError as exc:
            raise OSError(f"invalid editor command: {exc}") from exc

    # Windows backslashes are literal except before double quotes. shlex's
    # POSIX parser corrupts paths; its non-POSIX mode also mishandles quotes.
    import ctypes

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    parse = shell32.CommandLineToArgvW
    parse.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    parse.restype = ctypes.POINTER(ctypes.c_wchar_p)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    count = ctypes.c_int()
    argv = parse(editor.strip(), ctypes.byref(count))
    if not argv:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return list(argv[: count.value])
    finally:
        kernel32.LocalFree(argv)


def _call_windows_editor(cmd: List[str]) -> int:
    """Run native editors directly, and safely quote Windows batch launchers."""
    cmd[0] = shutil.which(cmd[0]) or cmd[0]
    if not cmd[0].lower().endswith((".cmd", ".bat")):
        return subprocess.call(cmd, shell=False)

    # Windows batch files always involve cmd.exe, even with shell=False.
    # Quote every argument, disable AutoRun/delayed expansion, and reject
    # characters that could be expanded or break quoting inside the launcher.
    if any(any(char in arg for char in '"%!\r\n\x00') for arg in cmd):
        raise OSError("batch editor arguments contain unsafe characters; use a native .exe editor")
    quoted = " ".join('"' + arg + "\\" * (len(arg) - len(arg.rstrip("\\"))) + '"' for arg in cmd)
    shell = ntpath.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
    command = f'{subprocess.list2cmdline([shell])} /d /v:off /s /c "{quoted}"'
    return subprocess.call(command, executable=shell, shell=False)


def _launch_editor(path: Path) -> None:
    """Launch the editor, wait for it, and fail if it exits unsuccessfully."""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    cmd = (
        _split_editor_command(editor)
        if editor
        else ["notepad" if sys.platform == "win32" else "vi"]
    )
    if not cmd or not cmd[0]:
        raise OSError("editor command is empty")
    cmd.append(str(path))
    if sys.platform == "win32":
        returncode = _call_windows_editor(cmd)
    else:
        returncode = subprocess.call(cmd, shell=False)
    if returncode:
        raise OSError(f"editor exited with status {returncode}")
