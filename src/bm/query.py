"""Pure filtering, search, and row-building operations for bookmark entries."""

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

from .utils import parse_iso, rid


@dataclass(frozen=True)
class FilterSpec:
    """Normalized filters shared by list, search, and export operations."""

    tag: Optional[str] = None
    host: str = ""
    path: str = ""
    since: Optional[datetime] = None


SEARCH_FIELDS = ("title", "url", "tags", "body")


def _normalize_path_arg(value: Optional[str]) -> str:
    """Normalize an optional path filter by removing surrounding slashes."""
    return value.strip("/") if value else ""


def _matches_tag(rel: Path, meta: Dict[str, Any], tag: Optional[str]) -> bool:
    if not tag:
        return True
    return tag in rel.parts[:-1] or tag in meta.get("tags", [])


def _matches_host(meta: Dict[str, Any], want_host: str) -> bool:
    if not want_host:
        return True
    host = urlparse(meta.get("url", "")).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    hq = want_host[4:] if want_host.startswith("www.") else want_host
    return host == hq


def _matches_since(meta: Dict[str, Any], since_dt: Optional[datetime]) -> bool:
    if since_dt is None:
        return True
    ts = parse_iso(meta.get("created")) or parse_iso(meta.get("modified"))
    return bool(ts and ts >= since_dt)


def _matches_path(rel: Path, path_prefix: str) -> bool:
    path_prefix = _normalize_path_arg(path_prefix)
    if not path_prefix:
        return True
    return str(rel).startswith(path_prefix + "/") or str(rel) == path_prefix


def passes_filters(rel: Path, meta: Dict[str, Any], filters: FilterSpec) -> bool:
    """Return whether an entry satisfies every configured filter."""
    return (
        _matches_tag(rel, meta, filters.tag)
        and _matches_host(meta, filters.host)
        and _matches_path(rel, filters.path)
        and _matches_since(meta, filters.since)
    )


def _build_row(rel: Path, meta: Dict[str, Any], timestamp: Optional[datetime]) -> Dict[str, Any]:
    url = meta.get("url", "")
    return {
        "id": rid(url),
        "path": str(rel),
        "title": meta.get("title", ""),
        "url": url,
        "tags": meta.get("tags", []),
        "created": meta.get("created", ""),
        "modified": meta.get("modified", ""),
        "_sort": timestamp or datetime.min.replace(tzinfo=timezone.utc),
    }


def sort_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort rows newest-first and remove their internal sort key."""
    rows.sort(key=lambda row: row["_sort"], reverse=True)
    for row in rows:
        row.pop("_sort", None)
    return rows


def collect_rows(
    entries: Iterable[Tuple[Path, Dict[str, Any]]], filters: FilterSpec
) -> List[Dict[str, Any]]:
    """Build and sort list rows from relative paths and metadata."""
    rows = []
    for rel, meta in entries:
        if not passes_filters(rel, meta, filters):
            continue
        timestamp = parse_iso(meta.get("created")) or parse_iso(meta.get("modified"))
        rows.append(_build_row(rel, meta, timestamp))
    return sort_rows(rows)


def _build_search_blob(meta: Dict[str, Any], body: str, fields: Tuple[str, ...]) -> str:
    parts = []
    for field in fields:
        if field == "title":
            parts.append(meta.get("title", ""))
        elif field == "url":
            parts.append(meta.get("url", ""))
        elif field == "tags":
            parts.append(" ".join(meta.get("tags", [])))
        elif field == "body":
            parts.append(body)
    return "\n".join(parts)


def _make_search_predicate(query: str, use_regex: bool) -> Callable[[str], bool]:
    """Return a predicate for the configured search mode.

    Invalid regular expressions intentionally propagate ``re.error`` so the
    command adapter can turn the domain-independent parser failure into the
    established CLI diagnostic.
    """
    if use_regex:
        pattern = re.compile(query, re.IGNORECASE)
        return lambda blob: bool(pattern.search(blob))
    terms = query.lower().split()
    return lambda blob: all(term in blob for term in terms)


def search_rows(
    entries: Iterable[Tuple[Path, Dict[str, Any], str]],
    filters: FilterSpec,
    query: str,
    fields: Tuple[str, ...],
    use_regex: bool,
) -> List[Dict[str, Any]]:
    """Build and sort search rows from entries with already-loaded bodies."""
    predicate = _make_search_predicate(query, use_regex)
    rows = []
    needs_lower = not use_regex
    for rel, meta, body in entries:
        if not passes_filters(rel, meta, filters):
            continue
        blob = _build_search_blob(meta, body, fields)
        if needs_lower:
            blob = blob.lower()
        if predicate(blob):
            timestamp = parse_iso(meta.get("created")) or parse_iso(meta.get("modified"))
            rows.append(_build_row(rel, meta, timestamp))
    return sort_rows(rows)
