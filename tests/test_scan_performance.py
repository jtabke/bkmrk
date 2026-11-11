"""Regression tests for avoiding unnecessary bookmark reads during scans."""

import builtins
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bm.backup import build_backup
from bm.commands import _collect_rows, _search_entries
from bm.doctor import inspect_store
from bm.exporting import write_json, write_netscape
from bm.io import build_text
from bm.query import SEARCH_FIELDS, FilterSpec, search_rows
from bm.store import Store
from bm.urls import ALLOWED_URL_SCHEMES


@pytest.mark.parametrize("operation", ["backup", "doctor"])
def test_backup_and_doctor_visit_each_directory_once(tmp_path, monkeypatch, operation):
    store = Store(tmp_path / "store")
    store.create()
    paths = ["dev/reading/entry", "news/entry"]
    for relative in paths:
        store.create_entry(
            store.path_for(relative), build_text({"url": "https://example.com/" + relative}, "")
        )
    metadata = store.root / ".git" / "objects" / "pack"
    metadata.mkdir(parents=True)
    (metadata / "unrelated").write_text("Git data is not bookmark content", encoding="utf-8")
    expected = {store.root, metadata, *metadata.parents}
    expected = {path for path in expected if path.is_relative_to(store.root)}
    expected.update([store.root / "dev", store.root / "dev/reading", store.root / "news"])
    scanned = Counter()
    original_scandir = os.scandir

    def count_scandir(path):
        scanned[Path(path)] += 1
        return original_scandir(path)

    monkeypatch.setattr(os, "scandir", count_scandir)
    if operation == "backup":
        build_backup(store)
    else:
        assert inspect_store(store, ALLOWED_URL_SCHEMES).document()["ok"]
    assert scanned == Counter({path: 1 for path in expected})


@pytest.mark.parametrize("operation", ["backup", "doctor"])
def test_traversal_failure_is_not_retried_or_silently_omitted(tmp_path, monkeypatch, operation):
    store = Store(tmp_path / "store")
    store.create()
    blocked = store.root / "blocked"
    blocked.mkdir()
    scanned = Counter()
    original_scandir = os.scandir

    def fail_scandir(path):
        scanned[Path(path)] += 1
        if Path(path) == blocked:
            raise PermissionError(13, "unreadable", str(blocked))
        return original_scandir(path)

    monkeypatch.setattr(os, "scandir", fail_scandir)
    if operation == "backup":
        with pytest.raises(PermissionError, match="unreadable"):
            build_backup(store)
    else:
        findings = inspect_store(store, ALLOWED_URL_SCHEMES).findings
        assert [item.code for item in findings] == ["unreadable_directory"]
    assert scanned == Counter({store.root: 1, blocked: 1})


@pytest.fixture
def scan_store(tmp_path, monkeypatch):
    store = Store(tmp_path / "store")
    paths = ["dev/keep", "dev/skip", "device/other", "news/other"]
    for relative in paths:
        path = store.path_for(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            build_text(
                {
                    "url": f"https://{'keep' if relative == 'dev/keep' else 'skip'}.example.com",
                    "title": "Example",
                    "tags": ["keep" if relative == "dev/keep" else "skip"],
                    "created": "2026-01-02" if relative == "dev/keep" else "2025-01-01",
                },
                "Python body\n",
            ),
            encoding="utf-8",
        )

    opened = []
    original_open = Path.open

    def track_open(path, mode="r", *args, **kwargs):
        opened.append((path.relative_to(store.root).with_suffix("").as_posix(), mode))
        return original_open(path, mode, *args, **kwargs)

    def track_header_open(path, mode="r", *args, **kwargs):
        opened.append((Path(path).relative_to(store.root).with_suffix("").as_posix(), mode))
        return builtins.open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", track_open)
    monkeypatch.setattr("bm.io.open", track_header_open, raising=False)
    return store, opened


def test_path_filter_does_not_traverse_unrelated_directories(scan_store, monkeypatch):
    store, _ = scan_store
    traversed = []
    original_rglob = Path.rglob

    def track_rglob(path, pattern, **kwargs):
        traversed.append(path)
        return original_rglob(path, pattern, **kwargs)

    monkeypatch.setattr(Path, "rglob", track_rglob)
    rows = _collect_rows(store, FilterSpec(path="dev"))

    assert sorted(row["path"] for row in rows) == ["dev/keep", "dev/skip"]
    assert traversed == [store.root / "dev"]


def test_prefix_includes_exact_bookmark_and_same_named_directory(tmp_path):
    store = Store(tmp_path)
    (tmp_path / "dev.bm").write_text(build_text({"title": "Exact"}, ""))
    (tmp_path / "dev").mkdir()
    (tmp_path / "dev" / "nested.bm").write_text(build_text({"title": "Nested"}, ""))

    assert sorted(
        entry.relative_path.as_posix() for entry in store.iter_entries(path_prefix="dev")
    ) == ["dev", "dev/nested"]


@pytest.mark.parametrize("prefix", ["dev//keep", "dev/../news", "../", "C:/dev", "dev/./keep"])
def test_unusual_prefix_retains_string_matching(scan_store, prefix):
    store, opened = scan_store
    assert _collect_rows(store, FilterSpec(path=prefix)) == []
    assert opened == []


@pytest.mark.parametrize("folder", ["dev[1]", "dev[ab]", "dev[!x]"])
def test_prefix_is_literal_not_a_glob(tmp_path, folder):
    store = Store(tmp_path)
    directory = tmp_path / folder
    directory.mkdir()
    (directory / "entry.bm").write_text(build_text({"title": "Literal prefix"}, ""))
    (tmp_path / "dev1").mkdir()
    (tmp_path / "dev1" / "other.bm").write_text(build_text({"title": "Not selected"}, ""))

    entries = list(store.iter_entries(path_prefix=folder))
    assert [entry.relative_path.as_posix() for entry in entries] == [f"{folder}/entry"]


@pytest.mark.parametrize("outward", [False, True])
def test_prefix_does_not_follow_directory_symlinks(tmp_path, outward):
    store = Store(tmp_path / "store")
    store.create()
    target = tmp_path / "outside" if outward else store.root / "real"
    target.mkdir()
    (target / "entry.bm").write_text(build_text({"title": "Hidden through alias"}, ""))
    (store.root / "alias").symlink_to(target, target_is_directory=True)
    (store.root / "alias.bm").write_text(build_text({"title": "Exact match"}, ""))

    entries = list(store.iter_entries(path_prefix="alias"))
    assert [entry.relative_path.as_posix() for entry in entries] == ["alias"]


def test_prefix_keeps_broken_bookmark_symlink_warning(tmp_path):
    store = Store(tmp_path)
    (tmp_path / "missing.bm").symlink_to(tmp_path / "absent.bm")
    warnings = []

    assert (
        list(
            store.iter_entries(
                path_prefix="missing", on_error=lambda rel, exc: warnings.append(rel)
            )
        )
        == []
    )
    assert warnings == [Path("missing")]


@pytest.mark.parametrize("fmt", ["json", "jsonl", "netscape"])
@pytest.mark.parametrize(
    "filters,expected_paths",
    [
        (FilterSpec(path="dev"), ["dev/keep", "dev/skip"]),
        (FilterSpec(path="/dev/"), ["dev/keep", "dev/skip"]),
        (FilterSpec(path="dev\\"), ["dev/keep", "dev/skip"]),
        (FilterSpec(path="dev/keep"), ["dev/keep"]),
        (FilterSpec(path="missing"), []),
        (FilterSpec(path="dev/../news"), []),
        (FilterSpec(path="dev", tag="keep"), ["dev/keep", "dev/skip"]),
        (FilterSpec(path="dev", host="keep.example.com"), ["dev/keep", "dev/skip"]),
        (
            FilterSpec(path="dev", since=datetime(2026, 1, 1, tzinfo=timezone.utc)),
            ["dev/keep", "dev/skip"],
        ),
        (FilterSpec(), ["dev/keep", "dev/skip", "device/other", "news/other"]),
    ],
)
def test_export_prefix_skips_reads_and_preserves_output(
    scan_store, monkeypatch, capsys, fmt, filters, expected_paths
):
    store, opened = scan_store
    original_iter = store.iter_entries

    def export():
        if fmt == "netscape":
            write_netscape(store, filters, sys.stdout)
        else:
            write_json(store, filters, sys.stdout, jsonl=fmt == "jsonl")

    def full_scan(**kwargs):
        kwargs["path_prefix"] = ""
        return original_iter(**kwargs)

    with monkeypatch.context() as context:
        context.setattr(store, "iter_entries", full_scan)
        export()
    expected_output = capsys.readouterr().out
    opened.clear()
    traversed = []
    original_rglob = Path.rglob

    def track_rglob(path, pattern, **kwargs):
        traversed.append(path)
        return original_rglob(path, pattern, **kwargs)

    monkeypatch.setattr(Path, "rglob", track_rglob)
    export()

    assert capsys.readouterr().out == expected_output
    assert sorted(path for path, _ in opened) == expected_paths
    assert all(mode == "rb" for _, mode in opened)
    if filters.path in {"dev", "/dev/", "dev\\"}:
        assert traversed == [store.root / "dev"]
    elif filters.path in {"dev/keep", "missing"}:
        assert traversed == []


def test_body_search_opens_each_entry_once(scan_store):
    store, opened = scan_store
    rows = search_rows(
        _search_entries(store, FilterSpec(), SEARCH_FIELDS), "Python", SEARCH_FIELDS, False
    )

    assert len(rows) == 4
    assert sorted(opened) == [(path, "r") for path in sorted(row["path"] for row in rows)]


@pytest.mark.parametrize("prefix", ["dev", "/dev/", "dev\\", "dev/keep"])
@pytest.mark.parametrize("operation", ["list", "search"])
def test_path_filter_skips_reads_outside_prefix(scan_store, prefix, operation):
    store, opened = scan_store
    filters = FilterSpec(path=prefix)
    if operation == "list":
        rows = _collect_rows(store, filters)
    else:
        rows = search_rows(
            _search_entries(store, filters, SEARCH_FIELDS), "Python", SEARCH_FIELDS, False
        )

    expected = ["dev/keep"] if prefix == "dev/keep" else ["dev/keep", "dev/skip"]
    assert sorted(row["path"] for row in rows) == expected
    assert sorted(path for path, _ in opened) == expected


@pytest.mark.parametrize(
    "filters",
    [
        FilterSpec(tag="keep"),
        FilterSpec(host="keep.example.com"),
        FilterSpec(since=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        FilterSpec(path="dev", tag="keep"),
    ],
)
def test_metadata_filters_read_bodies_only_for_matches(scan_store, filters):
    store, opened = scan_store
    rows = search_rows(
        _search_entries(store, filters, SEARCH_FIELDS), "Python", SEARCH_FIELDS, False
    )

    assert [row["path"] for row in rows] == ["dev/keep"]
    assert [path for path, mode in opened if mode == "r"] == ["dev/keep"]
    expected_headers = (
        ["dev/keep", "dev/skip"]
        if filters.path
        else ["dev/keep", "dev/skip", "device/other", "news/other"]
    )
    assert sorted(path for path, mode in opened if mode == "rb") == sorted(expected_headers)


def test_body_search_rejects_outward_symlinks(tmp_path, capsys):
    store = Store(tmp_path / "store")
    store.create()
    outside = tmp_path / "outside.bm"
    outside.write_text(build_text({"url": "https://outside.example.com"}, "Python"))
    (store.root / "outside.bm").symlink_to(outside)

    assert list(_search_entries(store, FilterSpec(), SEARCH_FIELDS)) == []
    assert "outside" in capsys.readouterr().err


def test_title_search_remains_metadata_only(scan_store):
    store, opened = scan_store
    fields = ("title",)
    rows = search_rows(_search_entries(store, FilterSpec(), fields), "Example", fields, False)

    assert len(rows) == 4
    assert len(opened) == 4
    assert all(mode == "rb" for _, mode in opened)


@pytest.mark.parametrize("meta_only,snapshot", [(False, False), (True, False), (False, True)])
def test_store_path_prefix_runs_before_loading(scan_store, meta_only, snapshot):
    store, opened = scan_store
    entries = list(
        store.iter_entries(meta_only=meta_only, snapshot=snapshot, path_prefix="dev/keep")
    )

    assert [entry.relative_path.as_posix() for entry in entries] == ["dev/keep"]
    assert len(opened) == 1
    assert opened[0][0] == "dev/keep"
    assert (entries[0].snapshot is not None) == snapshot
