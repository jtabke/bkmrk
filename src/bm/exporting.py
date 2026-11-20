"""Browser and pipeline serializers with explicit output destinations."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import TextIO, TypedDict

from .models import Metadata, metadata_tags, metadata_text
from .netscape import NETSCAPE_FOOTER, NETSCAPE_HEADER, build_netscape_tree
from .query import FilterSpec, passes_filters
from .store import Store


class ExportRow(TypedDict):
    """The metadata-only pipeline schema, intentionally without a stable ID."""

    path: str
    url: str
    title: str
    tags: list[str]
    created: str
    modified: str


def export_row(rel: Path, meta: Metadata) -> ExportRow:
    """Build the existing metadata-only pipeline schema, without a stable-ID field."""
    return {
        "path": rel.as_posix(),
        "url": metadata_text(meta, "url"),
        "title": metadata_text(meta, "title"),
        "tags": list(metadata_tags(meta)),
        "created": metadata_text(meta, "created"),
        "modified": metadata_text(meta, "modified"),
    }


def write_netscape(
    store: Store,
    filters: FilterSpec,
    stream: TextIO,
    *,
    on_error: Callable[[Path, Exception], None] | None = None,
) -> None:
    """Write browser HTML to the provided stream, keeping scan warnings separate."""
    entries = []
    for entry in store.iter_entries(meta_only=True, path_prefix=filters.path, on_error=on_error):
        if not passes_filters(entry.relative_path, entry.meta, filters, on_error=on_error):
            continue
        entries.append((entry.relative_path.as_posix(), entry.meta))
    html_body = build_netscape_tree(entries)
    stream.write(NETSCAPE_HEADER + html_body + NETSCAPE_FOOTER)


def write_json(
    store: Store,
    filters: FilterSpec,
    stream: TextIO,
    *,
    jsonl: bool = False,
    on_error: Callable[[Path, Exception], None] | None = None,
) -> None:
    """Write path-sorted JSON or scan-order JSONL without buffering the latter."""
    records = (
        export_row(entry.relative_path, entry.meta)
        for entry in store.iter_entries(meta_only=True, path_prefix=filters.path, on_error=on_error)
        if passes_filters(entry.relative_path, entry.meta, filters, on_error=on_error)
    )
    if jsonl:
        for row in records:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        return
    rows = sorted(records, key=lambda r: r["path"])
    json.dump(rows, stream, ensure_ascii=False)
    stream.write("\n")
