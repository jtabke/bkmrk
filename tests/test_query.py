"""Pure query rows retain their public schema before and after sorting."""

from copy import deepcopy
from pathlib import Path

from bm.exporting import export_row
from bm.query import FilterSpec, _build_row, collect_rows, search_rows, sort_rows


def test_rows_have_a_stable_schema_and_can_be_sorted_repeatedly():
    entries = [
        (Path("old"), {"url": "https://old.example", "created": "2024-01-01"}),
        (Path("invalid"), {"url": "https://invalid.example", "created": "bad"}),
        (Path("new"), {"url": "https://new.example", "modified": "2025-01-01"}),
        (Path("tied"), {"url": "https://tied.example", "created": "2024-01-01"}),
    ]
    rows = [_build_row(path, meta) for path, meta in entries]
    fields = {"id", "path", "title", "url", "tags", "created", "modified"}
    assert all(set(row) == fields for row in rows)
    contents = {row["id"]: deepcopy(row) for row in rows}
    assert sort_rows(rows) is rows
    assert [row["path"] for row in rows] == ["new", "old", "tied", "invalid"]
    assert {row["id"]: row for row in rows} == contents
    assert sort_rows(rows) == rows
    assert collect_rows(iter(entries), FilterSpec()) == rows
    assert search_rows(((path, meta, "") for path, meta in entries), "", ("url",), False) == rows


def test_query_and_export_rows_keep_distinct_schemas_and_detached_tags():
    meta = {"url": "https://example.com", "tags": ["ordered", "tags"], "custom": "keep"}
    query = _build_row(Path("entry"), meta)
    exported = export_row(Path("entry"), meta)
    assert set(query) == set(exported) | {"id"}
    query["tags"].append("query-only")
    exported["tags"].append("export-only")
    assert meta["tags"] == ["ordered", "tags"]
    assert meta["custom"] == "keep"
