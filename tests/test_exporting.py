"""Command and workflow contracts for exporting."""

import argparse
import sys
from io import StringIO
from pathlib import Path

import pytest

from bm.commands import (
    cmd_export,
    cmd_import,
)
from bm.exporting import write_json, write_netscape
from bm.io import load_entry
from bm.query import FilterSpec
from bm.store import Store, StoreEntry


@pytest.mark.parametrize("format", ["json", "jsonl", "netscape"])
def test_serializers_use_explicit_stream_without_writing_stdout(tmp_path, capsys, format):
    store = Store(tmp_path / "store")
    store.create()
    store.create_entry(store.path_for("entry"), "---\nurl: https://example.com\n---\n")
    stream = StringIO()
    if format == "netscape":
        write_netscape(store, FilterSpec(), stream)
    else:
        write_json(store, FilterSpec(), stream, jsonl=format == "jsonl")
    assert "https://example.com" in stream.getvalue()
    assert capsys.readouterr().out == ""


def test_jsonl_writes_each_record_before_reading_the_next(monkeypatch, tmp_path):
    stream = StringIO()
    store = Store(tmp_path)

    def entries(**kwargs):
        yield StoreEntry(
            tmp_path / "first.bm", Path("first"), {"url": "https://first.example.com"}, ""
        )
        assert "https://first.example.com" in stream.getvalue()
        yield StoreEntry(
            tmp_path / "second.bm", Path("second"), {"url": "https://second.example.com"}, ""
        )

    monkeypatch.setattr(store, "iter_entries", entries)
    write_json(store, FilterSpec(), stream, jsonl=True)
    assert len(stream.getvalue().splitlines()) == 2


class TestCmdExport:
    """Test cmd_export function."""

    def test_export_netscape_retains_title_url_tags(self, tmp_path, capsys):
        """Should export title, url, and tags in Netscape format."""
        store = tmp_path / "store"
        store.mkdir()

        # Create a bookmark
        content = """---
url: https://example.com
title: Example Title
tags: [tag1, tag2]
created: 2023-01-15T10:00:00Z
---
"""
        fpath = store / "test.bm"
        fpath.write_text(content)

        args = argparse.Namespace()
        args.store = str(store)
        args.fmt = "netscape"
        args.host = None
        args.since = None

        cmd_export(args)
        captured = capsys.readouterr()

        assert 'HREF="https://example.com"' in captured.out
        assert 'TAGS="tag1,tag2"' in captured.out
        assert ">Example Title</A>" in captured.out

    def test_export_json_schema_and_ordering(self, tmp_path, capsys):
        """Should export valid JSON with correct schema and ordering."""
        import json

        store = tmp_path / "store"
        store.mkdir()

        # Create bookmarks with different created dates
        content1 = """---
url: https://example1.com
title: Title 1
tags: [tag1]
created: 2023-01-15T10:00:00Z
modified: 2023-01-16T10:00:00Z
---
"""
        fpath1 = store / "b.bm"  # Will sort after a
        fpath1.write_text(content1)

        content2 = """---
url: https://example2.com
title: Title 2
tags: [tag2]
created: 2023-01-14T10:00:00Z
modified: 2023-01-15T10:00:00Z
---
"""
        fpath2 = store / "a.bm"  # Will sort before b
        fpath2.write_text(content2)

        args = argparse.Namespace()
        args.store = str(store)
        args.fmt = "json"
        args.host = None
        args.since = None
        args.jsonl = False

        cmd_export(args)
        captured = capsys.readouterr()
        output = captured.out

        # Should be valid JSON
        rows = json.loads(output)
        assert isinstance(rows, list)
        assert len(rows) == 2

        # Check schema of each row
        for row in rows:
            assert "path" in row
            assert "url" in row
            assert "title" in row
            assert "tags" in row
            assert "created" in row
            assert "modified" in row
            assert isinstance(row["tags"], list)

        # Should be sorted by path (a before b)
        assert rows[0]["path"] == "a"
        assert rows[1]["path"] == "b"

    def test_export_json_filters_by_host_and_tag(self, tmp_path, capsys):
        """`export json` should honor --host/--tag filters."""
        import argparse
        import json as _json

        store = tmp_path / "store"
        store.mkdir()
        (store / "a.bm").write_text("---\nurl: https://a.example.com\ntags: [keep]\n---\n")
        (store / "b.bm").write_text("---\nurl: https://b.example.com\ntags: [drop]\n---\n")

        args = argparse.Namespace(
            store=str(store),
            fmt="json",
            host="a.example.com",
            since=None,
            tag=None,
            path=None,
            jsonl=False,
        )
        cmd_export(args)
        rows = _json.loads(capsys.readouterr().out)
        assert [r["url"] for r in rows] == ["https://a.example.com"]

        args = argparse.Namespace(
            store=str(store),
            fmt="json",
            host=None,
            since=None,
            tag="keep",
            path=None,
            jsonl=False,
        )
        cmd_export(args)
        rows = _json.loads(capsys.readouterr().out)
        assert [r["url"] for r in rows] == ["https://a.example.com"]

    def test_export_jsonl_streams_one_per_line(self, tmp_path, capsys):
        """`--jsonl` should emit one JSON object per line and skip the array."""
        import argparse
        import json as _json

        store = tmp_path / "store"
        store.mkdir()
        (store / "a.bm").write_text("---\nurl: https://a.example.com\n---\n")
        (store / "b.bm").write_text("---\nurl: https://b.example.com\n---\n")

        args = argparse.Namespace(
            store=str(store),
            fmt="json",
            host=None,
            since=None,
            jsonl=True,
        )
        cmd_export(args)
        out = capsys.readouterr().out
        lines = [ln for ln in out.splitlines() if ln]
        assert len(lines) == 2
        urls = sorted(_json.loads(ln)["url"] for ln in lines)
        assert urls == ["https://a.example.com", "https://b.example.com"]

    def test_export_netscape_with_folders(self, tmp_path, capsys):
        """Should export bookmarks with folder hierarchies in Netscape format."""
        store = tmp_path / "store"
        store.mkdir()

        # Create bookmarks in folders
        (store / "dev").mkdir()
        (store / "dev" / "python").mkdir()
        (store / "news").mkdir()

        # Bookmark in dev/python/
        content1 = """---
url: https://fastapi.tiangolo.com
title: FastAPI
tags: [python, web]
created: 2023-01-15T10:00:00Z
---
"""
        (store / "dev" / "python" / "fastapi.bm").write_text(content1)

        # Bookmark in news/
        content2 = """---
url: https://news.ycombinator.com
title: Hacker News
tags: [news]
created: 2023-01-16T10:00:00Z
---
"""
        (store / "news" / "hn.bm").write_text(content2)

        # Bookmark at root
        content3 = """---
url: https://example.com
title: Root Bookmark
tags: []
created: 2023-01-17T10:00:00Z
---
"""
        (store / "root.bm").write_text(content3)

        args = argparse.Namespace()
        args.store = str(store)
        args.fmt = "netscape"
        args.host = None
        args.since = None

        cmd_export(args)
        captured = capsys.readouterr()

        output = captured.out
        # Check root bookmark
        assert 'HREF="https://example.com"' in output
        assert ">Root Bookmark</A>" in output

        # Check folder structure
        assert "<DT><H3>dev</H3>" in output
        assert "<DT><H3>news</H3>" in output
        assert "<DT><H3>python</H3>" in output

        # Check bookmarks in folders
        assert 'HREF="https://fastapi.tiangolo.com"' in output
        assert ">FastAPI</A>" in output
        assert 'HREF="https://news.ycombinator.com"' in output
        assert ">Hacker News</A>" in output

        # Verify nesting: dev contains python, which contains fastapi
        # The HTML should have dev > python > fastapi
        lines = output.splitlines()
        dev_index = next(i for i, line in enumerate(lines) if "<DT><H3>dev</H3>" in line)
        python_index = next(i for i, line in enumerate(lines) if "<DT><H3>python</H3>" in line)
        fastapi_index = next(
            i for i, line in enumerate(lines) if 'HREF="https://fastapi.tiangolo.com"' in line
        )
        assert dev_index < python_index < fastapi_index

    @pytest.mark.parametrize(
        "folder",
        [
            "folder&'quoted'",
            pytest.param(
                'folder&<"quoted">',
                marks=pytest.mark.skipif(
                    sys.platform == "win32",
                    reason="Windows forbids <, > and double quotes in filenames",
                ),
            ),
        ],
    )
    def test_export_import_netscape_escapes_special_characters(self, tmp_path, capsys, folder):
        """Netscape export/import preserves metadata and safely renders folder text."""
        import argparse
        from html import escape

        from bm.io import build_text

        source = tmp_path / "source"
        target = tmp_path / "target"
        source_folder = source / folder
        source_folder.mkdir(parents=True)
        target.mkdir()
        url = 'https://example.com/?q="quoted"&x=<tag>'
        title = 'A <title> & "quoted" with literal &amp; text'
        tags = ["rock&roll", "<angle>", "literal&#34;"]
        (source_folder / "entry.bm").write_text(
            build_text(
                {"url": url, "title": title, "tags": tags, "created": "2024-01-01T00:00:00Z"},
                "",
            ),
            encoding="utf-8",
        )

        cmd_export(
            argparse.Namespace(
                store=str(source),
                fmt="netscape",
                tag=None,
                host=None,
                since=None,
                path=None,
            )
        )
        exported = capsys.readouterr().out
        assert 'HREF="https://example.com/?q=&quot;quoted&quot;&amp;x=&lt;tag&gt;"' in exported
        assert 'TAGS="rock&amp;roll,&lt;angle&gt;,literal&amp;#34;"' in exported
        assert f"<H3>{escape(folder, quote=True)}</H3>" in exported
        assert "A &lt;title&gt; &amp; &quot;quoted&quot; with literal &amp;amp; text" in exported

        exported_file = tmp_path / "bookmarks.html"
        exported_file.write_text(exported, encoding="utf-8")
        cmd_import(argparse.Namespace(store=str(target), file=str(exported_file), force=False))
        capsys.readouterr()

        imported_files = list(target.rglob("*.bm"))
        assert len(imported_files) == 1
        # Import applies the same path normalization as CLI-created bookmarks.
        assert imported_files[0].parent.relative_to(target).as_posix() == "folderquoted"
        imported, _ = load_entry(imported_files[0])
        assert imported["url"] == url
        assert imported["title"] == title
        assert imported["tags"] == tags
