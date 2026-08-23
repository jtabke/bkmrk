"""Pure duplicate-selection and bookmark-group merge operations."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from .utils import parse_iso


def _entry_score(entry: Dict[str, Any]) -> Tuple[int, int, float, str]:
    """Return the stable score used to choose a duplicate survivor."""
    body_len = len(entry["body"].strip())
    title_len = len(entry["meta"].get("title", "").strip())
    created_dt = parse_iso(entry["meta"].get("created")) or parse_iso(entry["meta"].get("modified"))
    if created_dt and created_dt.tzinfo is None:
        created_dt = created_dt.replace(tzinfo=timezone.utc)
    created_ts = created_dt.timestamp() if created_dt else float("inf")
    return (-body_len, -title_len, created_ts, str(entry["rel"]))


def _select_survivor(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Choose the deterministic survivor from a normalized duplicate group."""
    return min(entries, key=_entry_score)


def _normalize_dt(dt: Optional[datetime]) -> Optional[datetime]:
    if dt and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _earliest_dt(current: Optional[datetime], candidate: Optional[datetime]) -> Optional[datetime]:
    candidate = _normalize_dt(candidate)
    if candidate is None:
        return current
    if current is None or candidate < current:
        return candidate
    return current


def _latest_dt(current: Optional[datetime], candidate: Optional[datetime]) -> Optional[datetime]:
    candidate = _normalize_dt(candidate)
    if candidate is None:
        return current
    if current is None or candidate > current:
        return candidate
    return current


def _collect_group_stats(
    entries: List[Dict[str, Any]],
) -> Tuple[Set[str], Optional[datetime], Optional[datetime], List[str]]:
    """Collect unioned tags, date bounds, and candidate titles for a group."""
    tags_union: Set[str] = set()
    earliest_created: Optional[datetime] = None
    latest_modified: Optional[datetime] = None
    title_candidates: List[str] = []

    for entry in entries:
        meta = entry["meta"]
        tags_union.update(t.strip() for t in meta.get("tags", []) if t.strip())
        tags_union.update(seg for seg in entry["rel"].parts[:-1] if seg)

        title = meta.get("title", "").strip()
        if title:
            title_candidates.append(title)

        earliest_created = _earliest_dt(earliest_created, parse_iso(meta.get("created")))
        latest_modified = _latest_dt(latest_modified, parse_iso(meta.get("modified")))

    return tags_union, earliest_created, latest_modified, title_candidates


def _collect_body_parts(
    entries: List[Dict[str, Any]], survivor: Dict[str, Any]
) -> Tuple[List[str], bool]:
    """Collect the survivor body and non-empty duplicate notes."""
    base_body = survivor["body"].rstrip()
    parts: List[str] = [base_body] if base_body else []
    notes_appended = False

    for entry in entries:
        if entry is survivor:
            continue
        extra_body = entry["body"].rstrip()
        if extra_body:
            notes_appended = True
            parts.append(f"[Merged from {entry['rel']}]\n{extra_body}".rstrip())

    return parts, notes_appended


def _join_body_parts(parts: List[str]) -> str:
    """Join merged note sections with one trailing newline."""
    clean = [part for part in parts if part]
    if not clean:
        return ""
    merged = "\n\n".join(clean)
    return merged.rstrip() + "\n"


def _merge_entry_group(
    entries: List[Dict[str, Any]], survivor: Dict[str, Any]
) -> Tuple[Dict[str, Any], str, List[str], bool, Optional[datetime], Optional[datetime]]:
    """Merge metadata and notes without mutating the input entries."""
    merged_meta = dict(survivor["meta"])
    tags_union, earliest_created, latest_modified, title_candidates = _collect_group_stats(entries)

    if title_candidates and not merged_meta.get("title"):
        title_candidates.sort(key=len, reverse=True)
        merged_meta["title"] = title_candidates[0]

    if earliest_created:
        merged_meta["created"] = earliest_created.isoformat()

    if latest_modified:
        merged_meta["modified"] = latest_modified.isoformat()

    body_parts, notes_appended = _collect_body_parts(entries, survivor)
    tags = sorted(t for t in tags_union if t)
    merged_body = _join_body_parts(body_parts)

    return merged_meta, merged_body, tags, notes_appended, earliest_created, latest_modified
