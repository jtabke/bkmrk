"""Pure filtering, search, and row-building operations for bookmark entries."""

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional, Tuple, TypedDict
from urllib.parse import urlparse

from .models import Metadata, metadata_tags, metadata_text
from .paths import matches_path_prefix
from .utils import parse_iso, rid


@dataclass(frozen=True)
class FilterSpec:
    """Normalized filters shared by list, search, and export operations."""

    tag: Optional[str] = None
    host: str = ""
    path: str = ""
    since: Optional[datetime] = None


class QueryRow(TypedDict):
    """The stable list/search schema, also used by interactive selection."""

    id: str
    path: str
    title: str
    url: str
    tags: list[str]
    created: str
    modified: str


SEARCH_FIELDS = ("title", "url", "tags", "body")


def _matches_tag(rel: Path, meta: Metadata, tag: Optional[str]) -> bool:
    if not tag:
        return True
    return tag in rel.parts[:-1] or tag in metadata_tags(meta)


def _matches_host(meta: Metadata, want_host: str) -> bool:
    if not want_host:
        return True
    host = urlparse(metadata_text(meta, "url")).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    hq = want_host[4:] if want_host.startswith("www.") else want_host
    return host == hq


def _matches_since(meta: Metadata, since_dt: Optional[datetime]) -> bool:
    if since_dt is None:
        return True
    ts = parse_iso(metadata_text(meta, "created")) or parse_iso(metadata_text(meta, "modified"))
    return bool(ts and ts >= since_dt)


def passes_filters(rel: Path, meta: Metadata, filters: FilterSpec) -> bool:
    """Return whether an entry satisfies every configured filter."""
    return (
        _matches_tag(rel, meta, filters.tag)
        and _matches_host(meta, filters.host)
        and matches_path_prefix(rel, filters.path)
        and _matches_since(meta, filters.since)
    )


def _build_row(rel: Path, meta: Metadata) -> QueryRow:
    url = metadata_text(meta, "url")
    return {
        "id": rid(url),
        "path": rel.as_posix(),
        "title": metadata_text(meta, "title"),
        "url": url,
        "tags": list(metadata_tags(meta)),
        "created": metadata_text(meta, "created"),
        "modified": metadata_text(meta, "modified"),
    }


def sort_rows(rows: list[QueryRow]) -> list[QueryRow]:
    """Sort newest-first without adding or consuming private row fields."""

    def timestamp(row: QueryRow) -> datetime:
        return (
            parse_iso(row["created"])
            or parse_iso(row["modified"])
            or datetime.min.replace(tzinfo=timezone.utc)
        )

    rows.sort(key=timestamp, reverse=True)
    return rows


def collect_rows(entries: Iterable[Tuple[Path, Metadata]], filters: FilterSpec) -> list[QueryRow]:
    """Build and sort list rows from relative paths and metadata."""
    rows = []
    for rel, meta in entries:
        if not passes_filters(rel, meta, filters):
            continue
        rows.append(_build_row(rel, meta))
    return sort_rows(rows)


def _build_search_blob(meta: Metadata, body: str, fields: Tuple[str, ...]) -> str:
    parts = []
    for field in fields:
        if field == "title":
            parts.append(metadata_text(meta, "title"))
        elif field == "url":
            parts.append(metadata_text(meta, "url"))
        elif field == "tags":
            parts.append(" ".join(metadata_tags(meta)))
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
    entries: Iterable[Tuple[Path, Metadata, str]],
    query: str,
    fields: Tuple[str, ...],
    use_regex: bool,
) -> list[QueryRow]:
    """Build and sort search rows from entries with already-loaded bodies."""
    predicate = _make_search_predicate(query, use_regex)
    rows = []
    needs_lower = not use_regex
    for rel, meta, body in entries:
        blob = _build_search_blob(meta, body, fields)
        if needs_lower:
            blob = blob.lower()
        if predicate(blob):
            rows.append(_build_row(rel, meta))
    return sort_rows(rows)
