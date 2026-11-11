"""Command and workflow contracts for dedupe."""

import argparse
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bm.cli import main
from bm.commands import (
    cmd_dedupe,
)
from bm.dedupe import _entry_score, apply_dedupe, plan_dedupe
from bm.errors import BmError
from bm.io import build_text, load_entry
from bm.store import Store, StoreEntry


def _entry(values):
    return StoreEntry(
        path=values["rel"].with_suffix(".bm"),
        relative_path=values["rel"],
        meta=values["meta"],
        body=values["body"],
    )


class TestEntryScore:
    def test_longer_body_wins(self):
        a = {"meta": {"title": "T"}, "body": "short", "rel": Path("a")}
        b = {"meta": {"title": "T"}, "body": "much longer body text here", "rel": Path("b")}
        # min by score → b wins because its score has more-negative body length
        assert _entry_score(_entry(b)) < _entry_score(_entry(a))

    def test_when_body_tied_longer_title_wins(self):
        a = {"meta": {"title": "T"}, "body": "x", "rel": Path("a")}
        b = {"meta": {"title": "Longer"}, "body": "x", "rel": Path("b")}
        assert _entry_score(_entry(b)) < _entry_score(_entry(a))

    def test_naive_created_normalized_to_utc(self):
        # Naive datetime in `created` should compare without raising and use UTC.
        e = {
            "meta": {"title": "T", "created": "2024-01-15T10:00:00"},
            "body": "x",
            "rel": Path("a"),
        }
        score = _entry_score(_entry(e))
        expected = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc).timestamp()
        assert score[2] == expected

    def test_falls_back_to_modified_when_created_missing(self):
        e = {
            "meta": {"title": "T", "modified": "2024-06-01T00:00:00+00:00"},
            "body": "x",
            "rel": Path("a"),
        }
        expected = datetime(2024, 6, 1, tzinfo=timezone.utc).timestamp()
        assert _entry_score(_entry(e))[2] == expected

    def test_no_dates_uses_inf(self):
        e = {"meta": {"title": "T"}, "body": "x", "rel": Path("a")}
        assert _entry_score(_entry(e))[2] == float("inf")

    def test_rel_is_final_tiebreaker(self):
        a = {"meta": {"title": "T"}, "body": "x", "rel": Path("a")}
        b = {"meta": {"title": "T"}, "body": "x", "rel": Path("b")}
        # Same body, title, and date → tiebreaker is the rel string.
        assert _entry_score(_entry(a)) < _entry_score(_entry(b))
        assert _entry_score(_entry(a))[3] == "a"
        assert _entry_score(_entry(b))[3] == "b"


def test_plan_and_apply_share_named_merge_without_mutating_original_snapshots(tmp_path):
    store = Store(tmp_path / "store")
    store.create()
    for slug, notes in [("first", "longer original notes"), ("other/second", "extra")]:
        store.create_entry(
            store.path_for(slug),
            build_text(
                {"url": "https://example.com", "tags": ["quoted'tag"], "x-extra": "kept"}, notes
            ),
        )
    originals = {path: path.read_bytes() for path in store.root.rglob("*.bm")}
    plan = plan_dedupe(store)
    assert plan.scanned == 2
    assert len(plan.groups) == 1
    group = plan.groups[0]
    assert group.merged.notes_appended
    assert group.merged.meta["x-extra"] == "kept"
    assert group.merged.meta["tags"] == ["other", "quoted'tag"]
    assert {path: path.read_bytes() for path in originals} == originals
    before = [(dict(entry.meta), entry.body, entry.snapshot) for entry in group.members]
    preview = group.document(dry_run=True)
    assert preview["dry_run"] is True

    apply_dedupe(store, plan)

    assert [(entry.meta, entry.body, entry.snapshot) for entry in group.members] == before
    assert list(store.root.rglob("*.bm")) == [group.survivor.path]
    meta, body = load_entry(group.survivor.path)
    assert meta["tags"] == group.merged.meta["tags"]
    assert meta["x-extra"] == "kept"
    assert "extra" in body and "Merged from other/second" in body
    assert "dry_run" not in group.document(dry_run=False)


@pytest.mark.parametrize("failure,status", [("conflict", 1), ("io", 2), ("domain", 7)])
def test_cli_late_dedupe_failure_reports_possible_partial_progress(
    tmp_path, monkeypatch, capsys, failure, status
):
    store = Store(tmp_path / "store")
    store.create()
    for slug, notes in [("survivor", "longest notes"), ("first", ""), ("second", "")]:
        store.create_entry(store.path_for(slug), build_text({"url": "https://example.com"}, notes))
    original_delete = Store.delete
    calls = 0
    stopped_path = None

    def fail_later(current_store, path, expected=None):
        nonlocal calls, stopped_path
        calls += 1
        if calls == 2:
            stopped_path = path
            if failure == "io":
                raise PermissionError("cannot remove duplicate")
            if failure == "domain":
                raise BmError("custom failure", exit_code=7)
            path.write_text("concurrent notes", encoding="utf-8")
        return original_delete(current_store, path, expected=expected)

    monkeypatch.setattr(Store, "delete", fail_later)
    assert main(["--store", str(store.root), "dedupe", "--json"]) == status
    output = capsys.readouterr()
    assert output.out == ""
    assert "dedupe stopped" in output.err
    assert "earlier changes may have been applied" in output.err
    assert "Inspect the store" in output.err
    assert "bm dedupe --dry-run" in output.err
    assert calls == 2 and stopped_path.exists()
    assert len(list(store.root.glob("*.bm"))) == 2
    assert store.path_for("survivor").exists()
    if failure == "conflict":
        assert stopped_path.read_text(encoding="utf-8") == "concurrent notes"


class TestCmdDedupe:
    """Test cmd_dedupe function."""

    def test_dedupe_merges_duplicates(self, tmp_path, capsys):
        """Should merge duplicate URLs and union tags/notes."""
        store = tmp_path / "store"
        store.mkdir()

        entry1 = store / "dev" / "example-one.bm"
        entry1.parent.mkdir(parents=True)
        entry1.write_text(
            """---
url: https://example.com/article?a=1&b=2
title: First title
tags: [alpha]
created: 2024-01-01T00:00:00+00:00
---
Primary notes
"""
        )

        entry2 = store / "inbox" / "example-two.bm"
        entry2.parent.mkdir(parents=True)
        entry2.write_text(
            """---
url: http://www.example.com:80/article?b=2&a=1
title: Second title
tags: [beta]
created: 2024-02-01T00:00:00+00:00
modified: 2024-03-01T00:00:00+00:00
---
Secondary notes
"""
        )

        args = argparse.Namespace()
        args.store = str(store)
        args.dry_run = False
        args.json = False

        cmd_dedupe(args)

        captured = capsys.readouterr()
        assert "duplicates for" in captured.out

        files = sorted(store.rglob("*.bm"))
        assert len(files) == 1

        meta, body = load_entry(files[0])
        assert meta["url"] in {
            "https://example.com/article?a=1&b=2",
            "http://www.example.com:80/article?b=2&a=1",
        }
        assert meta["created"] == "2024-01-01T00:00:00+00:00"
        assert meta["modified"] == "2024-03-01T00:00:00+00:00"
        assert meta["title"] in {"First title", "Second title"}
        assert meta["tags"] == ["alpha", "beta", "dev", "inbox"]
        assert "Primary notes" in body
        assert "Secondary notes" in body
        assert ("[Merged from inbox/example-two]" in body) or (
            "[Merged from dev/example-one]" in body
        )

    def test_dedupe_dry_run_keeps_files(self, tmp_path, capsys):
        """Dry run should not modify the store."""
        store = tmp_path / "store"
        store.mkdir()

        (store / "a").mkdir()
        (store / "b").mkdir()

        (store / "a" / "one.bm").write_text(
            """---
url: https://example.com
---
"""
        )
        (store / "b" / "two.bm").write_text(
            """---
url: http://example.com/
---
"""
        )

        args = argparse.Namespace()
        args.store = str(store)
        args.dry_run = True
        args.json = False

        cmd_dedupe(args)

        captured = capsys.readouterr()
        assert "DRY-RUN" in captured.out
        assert sorted(store.rglob("*.bm")) == [
            store / "a" / "one.bm",
            store / "b" / "two.bm",
        ]

    def test_dedupe_json_output(self, tmp_path, capsys):
        """Should emit JSON when requested."""
        store = tmp_path / "store"
        store.mkdir()

        (store / "one.bm").write_text(
            """---
url: https://example.com
---
"""
        )
        (store / "two.bm").write_text(
            """---
url: http://example.com
---
"""
        )

        args = argparse.Namespace()
        args.store = str(store)
        args.dry_run = True
        args.json = True

        cmd_dedupe(args)

        output = capsys.readouterr().out.strip()
        import json

        payload = json.loads(output)
        assert payload
        assert payload[0]["canonical_url"] == "example.com"
        assert payload[0]["dry_run"]


def test_preview_collections_are_detached_from_execution_plan(tmp_path):
    store = Store(tmp_path / "store")
    store.create()
    for name in ("first", "second"):
        store.create_entry(
            store.path_for(name),
            build_text({"url": "https://example.com", "tags": ["original"]}, ""),
        )
    plan = plan_dedupe(store)
    group = plan.groups[0]
    preview = group.document(dry_run=True)
    preview["tags"].append("preview-only")
    preview["removed"].clear()
    assert group.merged.meta["tags"] == ["original"]
    assert len(group.document(dry_run=True)["removed"]) == 1
    apply_dedupe(store, plan)
    meta, _ = load_entry(group.survivor.path)
    assert meta["tags"] == ["original"]
    assert len(list(store.root.rglob("*.bm"))) == 1
