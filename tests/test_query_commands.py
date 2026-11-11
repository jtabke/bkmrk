"""Command and workflow contracts for query_commands."""

import argparse

import pytest

from bm.commands import (
    cmd_list,
    cmd_search,
)
from bm.errors import BmError


class TestCmdList:
    """Test cmd_list function."""

    def test_list_host_filter_with_www(self, tmp_path, capsys):
        """Should filter by host with www. prefix."""
        store = tmp_path / "store"
        store.mkdir()
        # Create test bookmark
        content = """---
url: https://www.example.com/page
title: Test
---
"""
        fpath = store / "test.bm"
        fpath.write_text(content)

        args = argparse.Namespace()
        args.store = str(store)
        args.host = "www.example.com"
        args.tag = None
        args.since = None
        args.json = False
        args.jsonl = False

        cmd_list(args)
        captured = capsys.readouterr()
        assert "test" in captured.out

    def test_list_host_filter_without_www(self, tmp_path, capsys):
        """Should filter by host without www. prefix."""
        store = tmp_path / "store"
        store.mkdir()
        # Create test bookmark
        content = """---
url: https://www.example.com/page
title: Test
---
"""
        fpath = store / "test.bm"
        fpath.write_text(content)

        args = argparse.Namespace()
        args.store = str(store)
        args.host = "example.com"
        args.tag = None
        args.since = None
        args.json = False
        args.jsonl = False

        cmd_list(args)
        captured = capsys.readouterr()
        assert "test" in captured.out

    def test_list_since_date_only(self, tmp_path, capsys):
        """Should filter by date-only since."""
        store = tmp_path / "store"
        store.mkdir()
        # Create test bookmark with recent date
        content = """---
url: https://example.com
title: Test
created: 2023-01-15
---
"""
        fpath = store / "test.bm"
        fpath.write_text(content)

        args = argparse.Namespace()
        args.store = str(store)
        args.host = None
        args.tag = None
        args.since = "2023-01-10"
        args.json = False
        args.jsonl = False

        cmd_list(args)
        captured = capsys.readouterr()
        assert "test" in captured.out

    def test_list_since_full_iso(self, tmp_path, capsys):
        """Should filter by full ISO since."""
        store = tmp_path / "store"
        store.mkdir()
        # Create test bookmark
        content = """---
url: https://example.com
title: Test
created: 2023-01-15T10:00:00Z
---
"""
        fpath = store / "test.bm"
        fpath.write_text(content)

        args = argparse.Namespace()
        args.store = str(store)
        args.host = None
        args.tag = None
        args.since = "2023-01-15T09:00:00Z"
        args.json = False
        args.jsonl = False

        cmd_list(args)
        captured = capsys.readouterr()
        assert "test" in captured.out

    def test_list_json_returns_array(self, tmp_path, capsys):
        """Should return single JSON array for --json."""
        import json

        store = tmp_path / "store"
        store.mkdir()
        # Create test bookmarks
        content1 = """---
url: https://example1.com
title: Title 1
created: 2023-01-15T10:00:00Z
---
"""
        fpath1 = store / "a.bm"
        fpath1.write_text(content1)

        content2 = """---
url: https://example2.com
title: Title 2
created: 2023-01-16T10:00:00Z
---
"""
        fpath2 = store / "b.bm"
        fpath2.write_text(content2)

        args = argparse.Namespace()
        args.store = str(store)
        args.host = None
        args.tag = None
        args.since = None
        args.json = True
        args.jsonl = False

        cmd_list(args)
        captured = capsys.readouterr()
        output = captured.out

        # Should be valid JSON array
        rows = json.loads(output)
        assert isinstance(rows, list)
        assert len(rows) == 2

        # Should be sorted newest first (b before a)
        assert rows[0]["path"] == "b"
        assert rows[1]["path"] == "a"

    def test_list_jsonl_emits_objects_per_line(self, tmp_path, capsys):
        """Should emit one JSON object per line for --jsonl."""
        import json

        store = tmp_path / "store"
        store.mkdir()
        # Create test bookmarks
        content1 = """---
url: https://example1.com
title: Title 1
created: 2023-01-15T10:00:00Z
---
"""
        fpath1 = store / "a.bm"
        fpath1.write_text(content1)

        content2 = """---
url: https://example2.com
title: Title 2
created: 2023-01-16T10:00:00Z
---
"""
        fpath2 = store / "b.bm"
        fpath2.write_text(content2)

        args = argparse.Namespace()
        args.store = str(store)
        args.host = None
        args.tag = None
        args.since = None
        args.json = False
        args.jsonl = True

        cmd_list(args)
        captured = capsys.readouterr()
        output_lines = captured.out.strip().split("\n")

        # Should have 2 lines
        assert len(output_lines) == 2

        # Each line should be valid JSON
        obj1 = json.loads(output_lines[0])
        obj2 = json.loads(output_lines[1])

        # Should be sorted newest first (b before a)
        assert obj1["path"] == "b"
        assert obj2["path"] == "a"

    def test_list_skips_unreadable_files(self, tmp_path, capsys):
        """Should warn and continue when a .bm path cannot be read."""
        import argparse

        store = tmp_path / "store"
        store.mkdir()
        (store / "good.bm").write_text("---\nurl: https://good.example.com\n---\n")
        # A directory matching *.bm trips IsADirectoryError on read_text.
        (store / "bad.bm").mkdir()

        args = argparse.Namespace(
            store=str(store),
            host=None,
            tag=None,
            since=None,
            path=None,
            json=False,
            jsonl=False,
        )

        cmd_list(args)
        captured = capsys.readouterr()
        assert "good" in captured.out
        assert "skipping bad" in captured.err


class TestCmdSearch:
    """Test cmd_search function."""

    def test_search_regex_match(self, tmp_path, capsys):
        """`search --regex` should accept Python regex patterns."""
        import argparse

        store = tmp_path / "store"
        store.mkdir()
        (store / "a.bm").write_text("---\nurl: https://a.example.com\ntitle: rust async\n---\n")
        (store / "b.bm").write_text("---\nurl: https://b.example.com\ntitle: python sync\n---\n")

        args = argparse.Namespace(
            store=str(store),
            query=r"^rust\s",
            regex=True,
            field=None,
            tag=None,
            host=None,
            since=None,
            path=None,
            json=False,
            jsonl=False,
        )
        cmd_search(args)
        out = capsys.readouterr().out
        assert "a" in out
        assert "b\n" not in out

    def test_search_field_scope(self, tmp_path, capsys):
        """`search --field tags` should only match the tags field."""
        import argparse

        store = tmp_path / "store"
        store.mkdir()
        # title says "match" but tag does not
        (store / "a.bm").write_text(
            "---\nurl: https://a.example.com\ntitle: match me\ntags: [other]\n---\n"
        )
        # tag says "match" but title does not
        (store / "b.bm").write_text(
            "---\nurl: https://b.example.com\ntitle: nope\ntags: [match]\n---\n"
        )

        args = argparse.Namespace(
            store=str(store),
            query="match",
            regex=False,
            field=["tags"],
            tag=None,
            host=None,
            since=None,
            path=None,
            json=False,
            jsonl=False,
        )
        cmd_search(args)
        out = capsys.readouterr().out
        assert "b" in out
        assert "a\n" not in out

    def test_search_invalid_regex_dies(self, tmp_path):
        """An invalid regex must abort with code 2."""
        import argparse

        store = tmp_path / "store"
        store.mkdir()
        (store / "a.bm").write_text("---\nurl: https://a.example.com\n---\n")

        args = argparse.Namespace(
            store=str(store),
            query="(unbalanced",
            regex=True,
            field=None,
            tag=None,
            host=None,
            since=None,
            path=None,
            json=False,
            jsonl=False,
        )
        with pytest.raises(BmError) as exc_info:
            cmd_search(args)
        assert exc_info.value.exit_code == 2

    def test_search_zero_hits_exits_one(self, tmp_path):
        """No matches should exit code 1."""
        import argparse

        store = tmp_path / "store"
        store.mkdir()
        (store / "a.bm").write_text("---\nurl: https://a.example.com\n---\n")

        args = argparse.Namespace(
            store=str(store),
            query="nomatchpossible",
            regex=False,
            field=None,
            tag=None,
            host=None,
            since=None,
            path=None,
            json=False,
            jsonl=False,
        )
        with pytest.raises(BmError) as exc_info:
            cmd_search(args)
        assert exc_info.value.exit_code == 1

    def test_search_via_fixtures(self, store, args_factory, write_bm, capsys):
        """Smoke test of the conftest fixtures via cmd_search."""
        write_bm("a", url="https://a.example.com", title="match me")
        write_bm("b", url="https://b.example.com", title="other")

        args = args_factory(
            query="match",
            regex=False,
            field=None,
            tag=None,
            host=None,
            since=None,
            path=None,
            json=False,
            jsonl=False,
        )
        cmd_search(args)
        out = capsys.readouterr().out
        assert "a" in out
        assert "b\n" not in out

    def test_search_filters_by_tag(self, tmp_path, capsys):
        """`search` should honor the standard --tag filter."""
        import argparse

        store = tmp_path / "store"
        store.mkdir()
        (store / "a.bm").write_text(
            "---\nurl: https://a.example.com\ntags: [keep]\n---\nbody match\n"
        )
        (store / "b.bm").write_text(
            "---\nurl: https://b.example.com\ntags: [drop]\n---\nbody match\n"
        )

        args = argparse.Namespace(
            store=str(store),
            query="match",
            tag="keep",
            host=None,
            since=None,
            path=None,
            json=False,
            jsonl=False,
        )
        cmd_search(args)
        out = capsys.readouterr().out
        assert "a" in out
        assert "b\n" not in out

    def test_search_multi_term_and_logic(self, tmp_path, capsys):
        """Should use AND logic for multi-term search."""
        store = tmp_path / "store"
        store.mkdir()
        # Create test bookmark with both terms
        content = """---
url: https://example.com
title: Python programming tutorial
tags: [python, tutorial]
---
"""
        fpath = store / "test.bm"
        fpath.write_text(content)

        args = argparse.Namespace()
        args.store = str(store)
        args.query = "python tutorial"
        args.json = False
        args.jsonl = False

        cmd_search(args)
        captured = capsys.readouterr()
        assert "test" in captured.out

    def test_search_multi_term_missing_one_term(self, tmp_path, capsys):
        """Should not match if one term is missing."""
        store = tmp_path / "store"
        store.mkdir()
        # Create test bookmark with only one term
        content = """---
url: https://example.com
title: Python programming
tags: [python]
---
"""
        fpath = store / "test.bm"
        fpath.write_text(content)

        args = argparse.Namespace()
        args.store = str(store)
        args.query = "python tutorial"
        args.json = False
        args.jsonl = False

        with pytest.raises(BmError) as exc_info:
            cmd_search(args)
        assert exc_info.value.exit_code == 1
        captured = capsys.readouterr()
        assert "test" not in captured.out

    def test_search_json_returns_array(self, tmp_path, capsys):
        """Should return single JSON array for --json."""
        import json

        store = tmp_path / "store"
        store.mkdir()
        # Create test bookmarks
        content1 = """---
url: https://example1.com
title: Python tutorial
created: 2023-01-15T10:00:00Z
---
"""
        fpath1 = store / "a.bm"
        fpath1.write_text(content1)

        content2 = """---
url: https://example2.com
title: Python guide
created: 2023-01-16T10:00:00Z
---
"""
        fpath2 = store / "b.bm"
        fpath2.write_text(content2)

        args = argparse.Namespace()
        args.store = str(store)
        args.query = "python"
        args.json = True
        args.jsonl = False

        cmd_search(args)
        captured = capsys.readouterr()
        output = captured.out

        # Should be valid JSON array
        rows = json.loads(output)
        assert isinstance(rows, list)
        assert len(rows) == 2

        # Should be sorted newest first (b before a)
        assert rows[0]["path"] == "b"
        assert rows[1]["path"] == "a"

    def test_search_jsonl_emits_objects_per_line(self, tmp_path, capsys):
        """Should emit one JSON object per line for --jsonl."""
        import json

        store = tmp_path / "store"
        store.mkdir()
        # Create test bookmarks
        content1 = """---
url: https://example1.com
title: Python tutorial
created: 2023-01-15T10:00:00Z
---
"""
        fpath1 = store / "a.bm"
        fpath1.write_text(content1)

        content2 = """---
url: https://example2.com
title: Python guide
created: 2023-01-16T10:00:00Z
---
"""
        fpath2 = store / "b.bm"
        fpath2.write_text(content2)

        args = argparse.Namespace()
        args.store = str(store)
        args.query = "python"
        args.json = False
        args.jsonl = True

        cmd_search(args)
        captured = capsys.readouterr()
        output_lines = captured.out.strip().split("\n")

        # Should have 2 lines
        assert len(output_lines) == 2

        # Each line should be valid JSON
        obj1 = json.loads(output_lines[0])
        obj2 = json.loads(output_lines[1])

        # Should be sorted newest first (b before a)
        assert obj1["path"] == "b"
        assert obj2["path"] == "a"
