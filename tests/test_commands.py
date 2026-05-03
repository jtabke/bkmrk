"""Unit tests for bm.commands module."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from bm.commands import (
    cmd_add,
    cmd_dedupe,
    cmd_edit,
    cmd_export,
    cmd_import,
    cmd_init,
    cmd_list,
    cmd_mv,
    cmd_open,
    cmd_rm,
    cmd_search,
    cmd_show,
    cmd_sync,
    cmd_tag,
    cmd_tags,
    resolve_id_or_path,
)
from bm.io import load_entry


class TestCmdInit:
    """Test cmd_init function."""

    def test_init_basic(self, tmp_path):
        """Should create store directory."""
        store = tmp_path / "store"
        args = MagicMock()
        args.store = str(store)
        args.git = False

        cmd_init(args)

        assert store.exists()
        assert store.is_dir()
        readme = store / "README.txt"
        assert readme.exists()

    def test_init_sets_restrictive_modes(self, tmp_path):
        """Store dir should be 0o700 and README.txt 0o600 on POSIX."""
        import stat
        import sys as _sys

        if _sys.platform == "win32":
            return  # POSIX permissions not meaningful

        store = tmp_path / "store"
        args = MagicMock()
        args.store = str(store)
        args.git = False
        cmd_init(args)

        assert stat.S_IMODE(store.stat().st_mode) == 0o700
        assert stat.S_IMODE((store / "README.txt").stat().st_mode) == 0o600

    def test_init_with_git(self, tmp_path):
        """Should initialize git repo if requested."""
        from bm.commands import _git_cmd

        store = tmp_path / "store"
        args = MagicMock()
        args.store = str(store)
        args.git = True

        with patch("subprocess.run") as mock_run:
            cmd_init(args)

        mock_run.assert_called_once_with(_git_cmd("init"), cwd=store)

    def test_init_without_git_does_not_call_git(self, tmp_path):
        """Should not call git if not requested."""
        store = tmp_path / "store"
        args = MagicMock()
        args.store = str(store)
        args.git = False

        with patch("subprocess.run") as mock_run:
            cmd_init(args)

        mock_run.assert_not_called()

    def test_init_uses_default_store_when_none(self, tmp_path, monkeypatch):
        """Should use DEFAULT_STORE when --store is None."""
        fake_store = tmp_path / "default_store"
        monkeypatch.setattr("bm.commands.DEFAULT_STORE", fake_store)

        args = MagicMock()
        args.store = None  # No --store provided
        args.git = False

        cmd_init(args)

        assert fake_store.exists()
        assert fake_store.is_dir()


class TestCmdAdd:
    """Test cmd_add function."""

    def test_add_basic(self, tmp_path):
        """Should create bookmark file."""
        store = tmp_path / "store"
        store.mkdir()
        args = MagicMock()
        args.store = str(store)
        args.url = "https://example.com"
        args.id = None
        args.path = None
        args.name = "Example"
        args.tags = "tag1,tag2"
        args.description = "Notes"
        args.force = False
        args.edit = False

        with patch("bm.commands._launch_editor"):
            cmd_add(args)

        # Should create file
        files = list(store.glob("*.bm"))
        assert len(files) == 1
        fpath = files[0]
        content = fpath.read_text()
        assert "url: https://example.com" in content
        assert "title: Example" in content
        assert "tags: [tag1, tag2]" in content

    def test_add_force_overwrite(self, tmp_path):
        """Should overwrite with --force."""
        store = tmp_path / "store"
        store.mkdir()
        args = MagicMock()
        args.store = str(store)
        args.url = "https://example.com"
        args.id = None
        args.path = None
        args.name = "Example"
        args.tags = "tag1,tag2"
        args.description = "Notes"
        args.force = False
        args.edit = False

        with patch("bm.commands._launch_editor"):
            cmd_add(args)

        # Second add without force should fail
        with patch("bm.commands._launch_editor"):
            with pytest.raises(SystemExit):
                cmd_add(args)

        args.force = True
        with patch("bm.commands._launch_editor"):
            cmd_add(args)  # Should succeed

    def test_add_edit_replaces_fields_and_preserves_created(self, tmp_path):
        """Editor edits should fully replace meta (only created is preserved)."""
        import argparse

        store = tmp_path / "store"
        store.mkdir()
        args = argparse.Namespace(
            store=str(store),
            url="https://example.com",
            id=None,
            path=None,
            name="Original",
            tags="orig1,orig2",
            description="orig body",
            force=False,
            edit=True,
        )

        def fake_editor(path):
            # Simulate user keeping url+title, removing tags, setting notes.
            path.write_text("---\nurl: https://example.com\ntitle: Edited\n---\nedited body\n")

        with patch("bm.commands._launch_editor", side_effect=fake_editor):
            cmd_add(args)

        fpath = next(store.glob("*.bm"))
        content = fpath.read_text()
        assert "title: Edited" in content
        # Tags removed in editor must NOT survive merge.
        assert "tags:" not in content
        assert "edited body" in content
        # created from pre-edit meta must be preserved.
        assert "created:" in content

    def test_add_edit_warns_when_url_changed(self, tmp_path, capsys):
        """Should warn when editor changes the URL (slug/url mismatch)."""
        import argparse

        store = tmp_path / "store"
        store.mkdir()
        args = argparse.Namespace(
            store=str(store),
            url="https://original.example.com",
            id=None,
            path=None,
            name=None,
            tags=None,
            description=None,
            force=False,
            edit=True,
        )

        def change_url(path):
            path.write_text("---\nurl: https://changed.example.com\n---\n")

        with patch("bm.commands._launch_editor", side_effect=change_url):
            cmd_add(args)
        captured = capsys.readouterr()
        assert "url changed in editor" in captured.err

    def test_add_edit_dies_when_url_cleared(self, tmp_path):
        """Should refuse to write when editor leaves url blank."""
        import argparse

        store = tmp_path / "store"
        store.mkdir()
        args = argparse.Namespace(
            store=str(store),
            url="https://example.com",
            id=None,
            path=None,
            name=None,
            tags=None,
            description=None,
            force=False,
            edit=True,
        )

        def clear_url(path):
            path.write_text("---\ntitle: nothing\n---\n")

        with patch("bm.commands._launch_editor", side_effect=clear_url):
            with pytest.raises(SystemExit):
                cmd_add(args)


class TestResolveIdOrPath:
    """Test resolve_id_or_path function."""

    def test_resolve_by_id(self, tmp_path):
        """Should resolve by stable ID."""
        store = tmp_path / "store"
        store.mkdir()
        # Create a test file
        content = """---
url: https://example.com
---
"""
        fpath = store / "test.bm"
        fpath.write_text(content)

        from bm.utils import rid

        bookmark_id = rid("https://example.com")

        result = resolve_id_or_path(store, bookmark_id)
        assert result == fpath

    def test_resolve_by_path(self, tmp_path):
        """Should resolve by path."""
        store = tmp_path / "store"
        store.mkdir()
        fpath = store / "test.bm"
        fpath.write_text("content")

        result = resolve_id_or_path(store, "test")
        assert result == fpath

    def test_resolve_not_found(self, tmp_path):
        """Should return None for not found id."""
        store = tmp_path / "store"
        store.mkdir()
        result = resolve_id_or_path(store, "does-not-exist")
        assert result is None

    def test_resolve_id_wins_over_fuzzy(self, tmp_path):
        """Should prefer ID match over fuzzy path match."""
        store = tmp_path / "store"
        store.mkdir()
        # Create bookmark with specific URL
        content1 = """---
url: https://example.com/page1
---
"""
        fpath1 = store / "page1.bm"
        fpath1.write_text(content1)

        # Create another bookmark that would match fuzzy search
        content2 = """---
url: https://different.com/page1-suffix
---
"""
        fpath2 = store / "page1-suffix.bm"
        fpath2.write_text(content2)

        from bm.utils import rid

        # Use ID of first bookmark
        bookmark_id = rid("https://example.com/page1")
        result = resolve_id_or_path(store, bookmark_id)
        assert result == fpath1

    def test_resolve_fuzzy_ambiguous_dies(self, tmp_path, capsys):
        """Should abort with disambiguation list when fuzzy match is not unique."""
        store = tmp_path / "store"
        store.mkdir()
        (store / "prefix-suffix.bm").write_text("---\nurl: https://example1.com\n---\n")
        (store / "other-suffix.bm").write_text("---\nurl: https://example2.com\n---\n")

        with pytest.raises(SystemExit):
            resolve_id_or_path(store, "suffix")
        captured = capsys.readouterr()
        assert "ambiguous" in captured.err
        assert "prefix-suffix" in captured.err
        assert "other-suffix" in captured.err


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

        args = MagicMock()
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

        args = MagicMock()
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

        args = MagicMock()
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

        args = MagicMock()
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

        args = MagicMock()
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

        args = MagicMock()
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

        args = MagicMock()
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

        args = MagicMock()
        args.store = str(store)
        args.query = "python tutorial"
        args.json = False
        args.jsonl = False

        with pytest.raises(SystemExit) as exc_info:
            cmd_search(args)
        assert exc_info.value.code == 1
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

        args = MagicMock()
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

        args = MagicMock()
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


class TestCmdImport:
    """Test cmd_import function."""

    def test_import_netscape_round_trip_retains_title_url_tags(self, tmp_path):
        """Should retain title, url, and tags in Netscape round-trip."""
        store = tmp_path / "store"
        store.mkdir()

        # Create a Netscape bookmark file
        netscape_content = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
<DT><A HREF="https://example.com" TAGS="tag1,tag2">Example Title</A>
</DL><p>"""

        netscape_file = tmp_path / "bookmarks.html"
        netscape_file.write_text(netscape_content)

        args = MagicMock()
        args.store = str(store)
        args.fmt = "netscape"
        args.file = str(netscape_file)
        args.force = False

        cmd_import(args)

        # Check that bookmark was created
        files = list(store.glob("*.bm"))
        assert len(files) == 1
        fpath = files[0]

        meta, body = load_entry(fpath)
        assert meta["url"] == "https://example.com"
        assert meta["title"] == "Example Title"
        assert meta["tags"] == ["tag1", "tag2"]

    def test_import_progress_silent_when_not_a_tty(self, tmp_path, capsys):
        """Progress lines should be suppressed when stderr is not a TTY."""
        store = tmp_path / "store"
        store.mkdir()

        netscape_content = (
            "<!DOCTYPE NETSCAPE-Bookmark-file-1>\n"
            "<DL><p>\n"
            + "\n".join(f'<DT><A HREF="https://e{i}.example.com">E{i}</A>' for i in range(3))
            + "\n</DL><p>\n"
        )
        netscape_file = tmp_path / "bookmarks.html"
        netscape_file.write_text(netscape_content)

        args = MagicMock()
        args.store = str(store)
        args.fmt = "netscape"
        args.file = str(netscape_file)
        args.force = False

        cmd_import(args)
        captured = capsys.readouterr()
        # capsys non-TTY → no `\rbm: import:` progress lines
        assert "\rbm: import" not in captured.err

    def test_import_skips_unsafe_folder(self, tmp_path, capsys):
        """Should skip entries whose folder path is unsafe and continue."""
        store = tmp_path / "store"
        store.mkdir()

        netscape_content = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<DL><p>
<DT><H3>...</H3>
<DL><p>
<DT><A HREF="https://bad.example.com">Bad</A>
</DL><p>
<DT><A HREF="https://good.example.com">Good</A>
</DL><p>"""
        netscape_file = tmp_path / "bookmarks.html"
        netscape_file.write_text(netscape_content)

        args = MagicMock()
        args.store = str(store)
        args.fmt = "netscape"
        args.file = str(netscape_file)
        args.force = False

        cmd_import(args)

        files = list(store.rglob("*.bm"))
        assert len(files) == 1
        meta, _ = load_entry(files[0])
        assert meta["url"] == "https://good.example.com"
        captured = capsys.readouterr()
        assert "unsafe folder paths" in captured.err

    def test_import_skips_disallowed_scheme(self, tmp_path, capsys):
        """Should skip entries with javascript:/file: URLs and warn on stderr."""
        store = tmp_path / "store"
        store.mkdir()

        netscape_content = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
<DT><A HREF="javascript:alert(1)">XSS</A>
<DT><A HREF="file:///etc/passwd">Local</A>
<DT><A HREF="https://safe.example.com">Safe</A>
</DL><p>"""
        netscape_file = tmp_path / "bookmarks.html"
        netscape_file.write_text(netscape_content)

        args = MagicMock()
        args.store = str(store)
        args.fmt = "netscape"
        args.file = str(netscape_file)
        args.force = False

        cmd_import(args)

        files = list(store.glob("*.bm"))
        assert len(files) == 1
        meta, _ = load_entry(files[0])
        assert meta["url"] == "https://safe.example.com"
        captured = capsys.readouterr()
        assert "skipped 2 entries" in captured.err

    def test_import_netscape_add_date_respects_created(self, tmp_path):
        """Should use ADD_DATE for created timestamp."""
        store = tmp_path / "store"
        store.mkdir()

        # Create Netscape with ADD_DATE
        import time
        from datetime import datetime, timezone

        timestamp = int(time.time())
        expected_created = datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
        netscape_content = f"""<!DOCTYPE NETSCAPE-Bookmark-file-1>
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
<DT><A HREF="https://example.com" ADD_DATE="{timestamp}">Test</A>
</DL><p>"""

        netscape_file = tmp_path / "bookmarks.html"
        netscape_file.write_text(netscape_content)

        args = MagicMock()
        args.store = str(store)
        args.fmt = "netscape"
        args.file = str(netscape_file)
        args.force = False

        cmd_import(args)

        files = list(store.glob("*.bm"))
        assert len(files) == 1
        fpath = files[0]

        meta, body = load_entry(fpath)
        # Should have created timestamp from ADD_DATE
        assert meta["created"] == expected_created

    def test_import_netscape_overwrite_without_force_skips(self, tmp_path):
        """Should skip existing bookmark without --force."""
        store = tmp_path / "store"
        store.mkdir()

        # Create existing bookmark with the correct slug
        from bm.utils import create_slug_from_url

        slug = create_slug_from_url("https://existing.com")
        existing_content = """---
url: https://existing.com
title: Existing
---
"""
        existing_fpath = store / f"{slug}.bm"
        existing_fpath.write_text(existing_content)

        # Try to import bookmark with same URL
        netscape_content = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
<DT><A HREF="https://existing.com">New Title</A>
</DL><p>"""

        netscape_file = tmp_path / "bookmarks.html"
        netscape_file.write_text(netscape_content)

        args = MagicMock()
        args.store = str(store)
        args.fmt = "netscape"
        args.file = str(netscape_file)
        args.force = False

        cmd_import(args)

        # Existing bookmark should still exist unchanged
        meta, body = load_entry(existing_fpath)
        assert meta["title"] == "Existing"

    def test_import_netscape_overwrite_with_force_succeeds(self, tmp_path):
        """Should overwrite existing bookmark with --force."""
        store = tmp_path / "store"
        store.mkdir()

        # Create existing bookmark with the correct slug
        from bm.utils import create_slug_from_url

        slug = create_slug_from_url("https://existing.com")
        existing_content = """---
url: https://existing.com
title: Existing
---
"""
        existing_fpath = store / f"{slug}.bm"
        existing_fpath.write_text(existing_content)

        # Import bookmark with same URL
        netscape_content = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
<DT><A HREF="https://existing.com">New Title</A>
</DL><p>"""

        netscape_file = tmp_path / "bookmarks.html"
        netscape_file.write_text(netscape_content)

        args = MagicMock()
        args.store = str(store)
        args.fmt = "netscape"
        args.file = str(netscape_file)
        args.force = True

        cmd_import(args)

        # Existing bookmark should be updated
        meta, body = load_entry(existing_fpath)
        assert meta["title"] == "New Title"

    def test_import_netscape_tags_with_spaces_and_empty(self, tmp_path):
        """Should handle TAGS with spaces and empty items."""
        store = tmp_path / "store"
        store.mkdir()

        # Create Netscape with problematic TAGS
        netscape_content = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
<DT><A HREF="https://example.com" TAGS="a, ,b,  c  ,">Title</A>
</DL><p>"""

        netscape_file = tmp_path / "bookmarks.html"
        netscape_file.write_text(netscape_content)

        args = MagicMock()
        args.store = str(store)
        args.fmt = "netscape"
        args.file = str(netscape_file)
        args.force = False

        cmd_import(args)

        files = list(store.glob("*.bm"))
        assert len(files) == 1
        fpath = files[0]

        meta, body = load_entry(fpath)
        # Should strip spaces and remove empty items
        assert meta["tags"] == ["a", "b", "c"]

    def test_import_netscape_html_entities_in_title(self, tmp_path):
        """Should decode HTML entities in title."""
        store = tmp_path / "store"
        store.mkdir()

        # Create Netscape with HTML entities in title
        netscape_content = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
<DT><A HREF="https://example.com">&lt;Bold&gt; &amp; &quot;Quoted&quot;</A>
</DL><p>"""

        netscape_file = tmp_path / "bookmarks.html"
        netscape_file.write_text(netscape_content)

        args = MagicMock()
        args.store = str(store)
        args.fmt = "netscape"
        args.file = str(netscape_file)
        args.force = False

        cmd_import(args)

        files = list(store.glob("*.bm"))
        assert len(files) == 1
        fpath = files[0]

        meta, body = load_entry(fpath)
        # HTML entities should be decoded
        assert meta["title"] == '<Bold> & "Quoted"'

    def test_import_netscape_with_folders(self, tmp_path):
        """Should import Netscape HTML with folder hierarchies."""
        store = tmp_path / "store"
        store.mkdir()

        # Create Netscape HTML with folders
        netscape_content = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
<DT><A HREF="https://root.com">Root Bookmark</A>
<DT><H3>dev</H3>
<DL><p>
<DT><H3>python</H3>
<DL><p>
<DT><A HREF="https://fastapi.tiangolo.com" TAGS="python,web">FastAPI</A>
</DL><p>
</DL><p>
<DT><H3>news</H3>
<DL><p>
<DT><A HREF="https://news.ycombinator.com" TAGS="news">Hacker News</A>
</DL><p>
</DL><p>
"""

        netscape_file = tmp_path / "bookmarks.html"
        netscape_file.write_text(netscape_content)

        args = MagicMock()
        args.store = str(store)
        args.fmt = "netscape"
        args.file = str(netscape_file)
        args.force = False

        cmd_import(args)

        # Check root bookmark
        root_files = list(store.glob("*.bm"))
        assert len(root_files) == 1
        meta, _ = load_entry(root_files[0])
        assert meta["url"] == "https://root.com"
        assert meta["title"] == "Root Bookmark"

        # Check folder structure exists
        assert (store / "dev").is_dir()
        assert (store / "dev" / "python").is_dir()
        assert (store / "news").is_dir()

        # Check dev/python/ has one file
        python_files = list((store / "dev" / "python").glob("*.bm"))
        assert len(python_files) == 1
        meta, _ = load_entry(python_files[0])
        assert meta["url"] == "https://fastapi.tiangolo.com"
        assert meta["title"] == "FastAPI"
        assert meta["tags"] == ["python", "web"]

        # Check news/ has one file
        news_files = list((store / "news").glob("*.bm"))
        assert len(news_files) == 1
        meta, _ = load_entry(news_files[0])
        assert meta["url"] == "https://news.ycombinator.com"
        assert meta["title"] == "Hacker News"
        assert meta["tags"] == ["news"]

    def test_import_netscape_h3_with_attributes(self, tmp_path):
        """Should parse H3 tags with attributes."""
        store = tmp_path / "store"
        store.mkdir()

        # Create Netscape HTML with H3 having attributes
        netscape_content = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
<DT><H3 class="folder" id="dev">dev</H3>
<DL><p>
<DT><A HREF="https://example.com">Test</A>
</DL><p>
</DL><p>
"""

        netscape_file = tmp_path / "bookmarks.html"
        netscape_file.write_text(netscape_content)

        args = MagicMock()
        args.store = str(store)
        args.fmt = "netscape"
        args.file = str(netscape_file)
        args.force = False

        cmd_import(args)

        # Check that folder was created despite attributes
        assert (store / "dev").is_dir()
        dev_files = list((store / "dev").glob("*.bm"))
        assert len(dev_files) == 1
        meta, _ = load_entry(dev_files[0])
        assert meta["url"] == "https://example.com"
        assert meta["title"] == "Test"


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

        args = MagicMock()
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

        args = MagicMock()
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
        with pytest.raises(SystemExit) as exc_info:
            cmd_search(args)
        assert exc_info.value.code == 2

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
        with pytest.raises(SystemExit) as exc_info:
            cmd_search(args)
        assert exc_info.value.code == 1

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

        args = MagicMock()
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


class TestCmdOpen:
    """Test cmd_open function."""

    def test_open_successful(self, tmp_path, capsys):
        """Should open URL in browser successfully."""
        store = tmp_path / "store"
        store.mkdir()
        # Create test bookmark with URL
        content = """---
url: https://example.com
title: Test
---
"""
        fpath = store / "test.bm"
        fpath.write_text(content)

        args = MagicMock()
        args.store = str(store)
        args.id = "test"

        with patch("webbrowser.open", return_value=True) as mock_open:
            cmd_open(args)

        mock_open.assert_called_once_with("https://example.com")
        captured = capsys.readouterr()
        assert "https://example.com" in captured.out

    def test_open_missing_url(self, tmp_path):
        """Should error when entry missing URL."""
        store = tmp_path / "store"
        store.mkdir()
        # Create test bookmark without URL
        content = """---
title: Test
---
"""
        fpath = store / "test.bm"
        fpath.write_text(content)

        args = MagicMock()
        args.store = str(store)
        args.id = "test"

        with pytest.raises(SystemExit):
            cmd_open(args)

    def test_open_browser_failure_warning(self, tmp_path, capsys):
        """Should warn when browser open fails."""
        store = tmp_path / "store"
        store.mkdir()
        # Create test bookmark with URL
        content = """---
url: https://example.com
title: Test
---
"""
        fpath = store / "test.bm"
        fpath.write_text(content)

        args = MagicMock()
        args.store = str(store)
        args.id = "test"

        with patch("webbrowser.open", return_value=False) as mock_open:
            cmd_open(args)

        mock_open.assert_called_once_with("https://example.com")
        captured = capsys.readouterr()
        assert "https://example.com" in captured.out
        assert "warning: system did not acknowledge opening browser" in captured.err

    def test_open_refuses_disallowed_scheme(self, tmp_path, capsys):
        """Should refuse to open javascript: URLs without --allow-scheme."""
        import argparse

        store = tmp_path / "store"
        store.mkdir()
        (store / "evil.bm").write_text("---\nurl: javascript:alert(1)\n---\n")

        args = argparse.Namespace(store=str(store), id="evil", allow_scheme=False)

        with patch("webbrowser.open") as mock_open, pytest.raises(SystemExit):
            cmd_open(args)
        mock_open.assert_not_called()
        captured = capsys.readouterr()
        assert "refusing to open 'javascript' URL" in captured.err

    def test_open_allow_scheme_override(self, tmp_path):
        """Should open disallowed scheme when --allow-scheme is set."""
        import argparse

        store = tmp_path / "store"
        store.mkdir()
        (store / "evil.bm").write_text("---\nurl: javascript:alert(1)\n---\n")

        args = argparse.Namespace(store=str(store), id="evil", allow_scheme=True)

        with patch("webbrowser.open", return_value=True) as mock_open:
            cmd_open(args)
        mock_open.assert_called_once_with("javascript:alert(1)")


class TestCmdShow:
    """Test cmd_show function."""

    def test_show_full_entry(self, tmp_path, capsys):
        """Should show all fields when present."""
        store = tmp_path / "store"
        store.mkdir()
        # Create test bookmark with all fields
        content = """---
url: https://example.com
title: Example Title
tags: [tag1, tag2, tag3]
created: 2023-01-15T10:00:00Z
modified: 2023-01-16T11:00:00Z
---
This is the body content.
"""
        fpath = store / "test.bm"
        fpath.write_text(content)

        args = MagicMock()
        args.store = str(store)
        args.id = "test"

        cmd_show(args)
        captured = capsys.readouterr()
        output = captured.out

        assert "# test" in output
        assert "url: https://example.com" in output
        assert "title: Example Title" in output
        assert "tags: tag1, tag2, tag3" in output
        assert "created: 2023-01-15T10:00:00Z" in output
        assert "modified: 2023-01-16T11:00:00Z" in output
        assert "\nThis is the body content." in output

    def test_show_missing_fields_omitted(self, tmp_path, capsys):
        """Should omit missing or empty fields."""
        store = tmp_path / "store"
        store.mkdir()
        # Create test bookmark with only some fields
        content = """---
url: https://example.com
title: Example Title
---
This is the body content.
"""
        fpath = store / "test.bm"
        fpath.write_text(content)

        args = MagicMock()
        args.store = str(store)
        args.id = "test"

        cmd_show(args)
        captured = capsys.readouterr()
        output = captured.out

        assert "# test" in output
        assert "url: https://example.com" in output
        assert "title: Example Title" in output
        # Should not contain tags, created, modified
        assert "tags:" not in output
        assert "created:" not in output
        assert "modified:" not in output
        assert "\nThis is the body content." in output

    def test_show_empty_body_omitted(self, tmp_path, capsys):
        """Should omit empty body."""
        store = tmp_path / "store"
        store.mkdir()
        # Create test bookmark with empty body
        content = """---
url: https://example.com
title: Example Title
---
"""
        fpath = store / "test.bm"
        fpath.write_text(content)

        args = MagicMock()
        args.store = str(store)
        args.id = "test"

        cmd_show(args)
        captured = capsys.readouterr()
        output = captured.out

        assert "# test" in output
        assert "url: https://example.com" in output
        assert "title: Example Title" in output
        # Should not have extra newline for empty body
        assert output.strip().endswith("title: Example Title")


class TestCmdEdit:
    """Test cmd_edit function."""

    def test_edit_bumps_modified_timestamp(self, tmp_path):
        """Should bump modified timestamp after editor returns."""
        store = tmp_path / "store"
        store.mkdir()
        # Create test bookmark with existing modified timestamp
        old_modified = "2023-01-15T10:00:00+00:00"
        content = f"""---
url: https://example.com
title: Test
modified: {old_modified}
---
"""
        fpath = store / "test.bm"
        fpath.write_text(content)

        args = MagicMock()
        args.store = str(store)
        args.id = "test"

        # Mock the editor to do nothing
        with patch("bm.commands._launch_editor"):
            # Mock iso_now to return a specific timestamp
            new_modified = "2023-01-16T11:00:00+00:00"
            with patch("bm.commands.iso_now", return_value=new_modified):
                cmd_edit(args)

        # Check that modified was updated
        meta, body = load_entry(fpath)
        assert meta["modified"] == new_modified
        assert meta["modified"] != old_modified

    def test_edit_creates_modified_if_missing(self, tmp_path):
        """Should create modified timestamp if it doesn't exist."""
        store = tmp_path / "store"
        store.mkdir()
        # Create test bookmark without modified timestamp
        content = """---
url: https://example.com
title: Test
---
"""
        fpath = store / "test.bm"
        fpath.write_text(content)

        args = MagicMock()
        args.store = str(store)
        args.id = "test"

        # Mock the editor to do nothing
        with patch("bm.commands._launch_editor"):
            # Mock iso_now to return a specific timestamp
            new_modified = "2023-01-16T11:00:00+00:00"
            with patch("bm.commands.iso_now", return_value=new_modified):
                cmd_edit(args)

        # Check that modified was added
        meta, body = load_entry(fpath)
        assert meta["modified"] == new_modified


class TestCmdRm:
    """Test cmd_rm function."""

    def test_rm_removes_file(self, tmp_path):
        """Should remove the bookmark file."""
        store = tmp_path / "store"
        store.mkdir()
        fpath = store / "test.bm"
        fpath.write_text("content")

        args = MagicMock()
        args.store = str(store)
        args.id = "test"

        cmd_rm(args)

        assert not fpath.exists()

    def test_rm_prunes_empty_parent_dirs(self, tmp_path):
        """Should prune empty parent directories."""
        store = tmp_path / "store"
        store.mkdir()
        # Create nested directory structure
        nested_dir = store / "folder" / "subfolder"
        nested_dir.mkdir(parents=True)
        fpath = nested_dir / "test.bm"
        fpath.write_text("content")

        args = MagicMock()
        args.store = str(store)
        args.id = "folder/subfolder/test"

        cmd_rm(args)

        # File should be gone
        assert not fpath.exists()
        # Empty directories should be pruned
        assert not nested_dir.exists()
        assert not (store / "folder").exists()

    def test_rm_stops_pruning_at_store_root(self, tmp_path):
        """Should stop pruning at store root."""
        store = tmp_path / "store"
        store.mkdir()
        fpath = store / "test.bm"
        fpath.write_text("content")

        args = MagicMock()
        args.store = str(store)
        args.id = "test"

        cmd_rm(args)

        # File should be gone
        assert not fpath.exists()
        # Store directory should still exist
        assert store.exists()


class TestCmdMv:
    """Test cmd_mv function."""

    def test_mv_moves_file(self, tmp_path, capsys):
        """Should move file to new location."""
        store = tmp_path / "store"
        store.mkdir()
        src_path = store / "old.bm"
        src_path.write_text("content")

        args = MagicMock()
        args.store = str(store)
        args.src = "old"
        args.dst = "new"
        args.force = False

        cmd_mv(args)

        # Old file should be gone
        assert not src_path.exists()
        # New file should exist
        new_path = store / "new.bm"
        assert new_path.exists()
        assert new_path.read_text() == "content"
        # Should print relative path
        captured = capsys.readouterr()
        assert "new" in captured.out

    def test_mv_creates_parent_dirs(self, tmp_path):
        """Should create parent directories for destination."""
        store = tmp_path / "store"
        store.mkdir()
        src_path = store / "old.bm"
        src_path.write_text("content")

        args = MagicMock()
        args.store = str(store)
        args.src = "old"
        args.dst = "folder/subfolder/new"
        args.force = False

        cmd_mv(args)

        # Should create nested directories
        new_path = store / "folder" / "subfolder" / "new.bm"
        assert new_path.exists()

    def test_mv_collision_without_force_fails(self, tmp_path):
        """Should fail on collision without --force."""
        store = tmp_path / "store"
        store.mkdir()
        src_path = store / "old.bm"
        src_path.write_text("content")
        dst_path = store / "existing.bm"
        dst_path.write_text("existing content")

        args = MagicMock()
        args.store = str(store)
        args.src = "old"
        args.dst = "existing"
        args.force = False

        with pytest.raises(SystemExit):
            cmd_mv(args)

        # Source should still exist
        assert src_path.exists()
        # Destination should still exist
        assert dst_path.exists()

    def test_mv_collision_with_force_succeeds(self, tmp_path):
        """Should succeed on collision with --force."""
        store = tmp_path / "store"
        store.mkdir()
        src_path = store / "old.bm"
        src_path.write_text("content")
        dst_path = store / "existing.bm"
        dst_path.write_text("existing content")

        args = MagicMock()
        args.store = str(store)
        args.src = "old"
        args.dst = "existing"
        args.force = True

        cmd_mv(args)

        # Source should be gone
        assert not src_path.exists()
        # Destination should have new content
        assert dst_path.exists()
        assert dst_path.read_text() == "content"

    def test_mv_destination_escaping_prevented(self, tmp_path):
        """Should prevent destination escaping store."""
        store = tmp_path / "store"
        store.mkdir()
        src_path = store / "old.bm"
        src_path.write_text("content")

        args = MagicMock()
        args.store = str(store)
        args.src = "old"
        args.dst = "../../../outside"
        args.force = False

        with pytest.raises(SystemExit):
            cmd_mv(args)

        # Source should still exist
        assert src_path.exists()

    def test_mv_refuses_symlink_source(self, tmp_path):
        """Should refuse to move when source is a symlink."""
        store = tmp_path / "store"
        store.mkdir()
        target = tmp_path / "outside.bm"
        target.write_text("---\nurl: https://outside.example.com\n---\n")
        link = store / "linked.bm"
        link.symlink_to(target)

        args = MagicMock()
        args.store = str(store)
        args.src = "linked"
        args.dst = "elsewhere"
        args.force = False

        with pytest.raises(SystemExit):
            cmd_mv(args)
        assert link.is_symlink()
        assert target.exists()

    def test_mv_prunes_empty_source_dir(self, tmp_path):
        """Should prune empty parent directories left behind by move."""
        store = tmp_path / "store"
        store.mkdir()
        src_dir = store / "dev" / "python"
        src_dir.mkdir(parents=True)
        (src_dir / "x.bm").write_text("---\nurl: https://x.example.com\n---\n")

        args = MagicMock()
        args.store = str(store)
        args.src = "dev/python/x"
        args.dst = "news/x"
        args.force = False

        cmd_mv(args)

        assert (store / "news" / "x.bm").exists()
        assert not src_dir.exists()
        assert not (store / "dev").exists()


class TestCmdTags:
    """Test cmd_tags function."""

    def test_tags_unions_folder_and_header_tags(self, tmp_path, capsys):
        """Should union folder path segments and header tags."""
        store = tmp_path / "store"
        store.mkdir()
        # Create bookmark in folder with header tags
        folder_dir = store / "web" / "python"
        folder_dir.mkdir(parents=True)
        content = """---
url: https://example.com
tags: [python, tutorial, web]
---
"""
        fpath = folder_dir / "test.bm"
        fpath.write_text(content)

        # Create another bookmark in different folder
        other_dir = store / "docs"
        other_dir.mkdir()
        content2 = """---
url: https://docs.com
tags: [documentation, web]
---
"""
        fpath2 = other_dir / "docs.bm"
        fpath2.write_text(content2)

        args = MagicMock()
        args.store = str(store)

        cmd_tags(args)
        captured = capsys.readouterr()
        output_lines = captured.out.strip().split("\n")

        # Should include folder tags: web, python, docs
        # Header tags: python, tutorial, web, documentation, web
        # Union and sorted: docs, documentation, python, tutorial, web
        expected_tags = ["docs", "documentation", "python", "tutorial", "web"]
        assert output_lines == expected_tags

    def test_tags_dedups_and_sorts(self, tmp_path, capsys):
        """Should deduplicate and sort tags."""
        store = tmp_path / "store"
        store.mkdir()
        # Create bookmark with duplicate tags in different places
        content = """---
url: https://example.com
tags: [zebra, alpha, zebra]
---
"""
        fpath = store / "alpha" / "test.bm"  # alpha appears in path and tags
        fpath.parent.mkdir(parents=True)
        fpath.write_text(content)

        args = MagicMock()
        args.store = str(store)

        cmd_tags(args)
        captured = capsys.readouterr()
        output_lines = captured.out.strip().split("\n")

        # Should be sorted and deduped: alpha, zebra
        expected_tags = ["alpha", "zebra"]
        assert output_lines == expected_tags


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

        args = MagicMock()
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

        args = MagicMock()
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

        args = MagicMock()
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


class TestCmdDirs:
    """Test cmd_dirs function."""

    def test_dirs_lists_unique_prefixes(self, store, args_factory, write_bm, capsys):
        from bm.commands import cmd_dirs

        write_bm("dev/python/x", url="https://x")
        write_bm("dev/web/y", url="https://y")
        write_bm("news/z", url="https://z")
        write_bm("root", url="https://r")

        args = args_factory(json=False)
        cmd_dirs(args)
        out = capsys.readouterr().out.splitlines()
        assert out == ["dev", "dev/python", "dev/web", "news"]

    def test_dirs_json_output(self, store, args_factory, write_bm, capsys):
        import json as _json

        from bm.commands import cmd_dirs

        write_bm("dev/python/x", url="https://x")
        args = args_factory(json=True)
        cmd_dirs(args)
        assert _json.loads(capsys.readouterr().out) == ["dev", "dev/python"]


class TestNotFoundDies:
    """Each lookup command should die cleanly when the id is unknown."""

    @pytest.mark.parametrize(
        "ctor, extra",
        [
            ("show", {}),
            ("open", {"allow_scheme": False}),
            ("edit", {}),
            ("rm", {}),
            ("tag", {"action": "add", "tags": ["x"]}),
        ],
    )
    def test_unknown_id_exits(self, store, args_factory, ctor, extra):
        import bm.commands as cmds

        fn = getattr(cmds, f"cmd_{ctor}")
        args = args_factory(id="ghost", **extra)
        with pytest.raises(SystemExit):
            fn(args)


class TestStoreMissingDies:
    """Commands that explicitly check for the store should die when it's gone."""

    @pytest.mark.parametrize(
        "name, extra",
        [
            (
                "list",
                {
                    "json": False,
                    "jsonl": False,
                    "tag": None,
                    "host": None,
                    "since": None,
                    "path": None,
                },
            ),
            ("dedupe", {"json": False, "dry_run": False}),
        ],
    )
    def test_missing_store(self, tmp_path, name, extra):
        import argparse

        import bm.commands as cmds

        args = argparse.Namespace(store=str(tmp_path / "nope"), **extra)
        with pytest.raises(SystemExit):
            getattr(cmds, f"cmd_{name}")(args)


class TestCmdSync:
    """Test cmd_sync function."""

    def test_sync_error_when_not_git_repo(self, tmp_path):
        """Should error when store is not a git repo."""
        store = tmp_path / "store"
        store.mkdir()

        args = MagicMock()
        args.store = str(store)

        with pytest.raises(SystemExit) as exc_info:
            cmd_sync(args)
        assert exc_info.value.code == 2

    def test_sync_success_adds_and_commits(self, tmp_path):
        """Should run git add and commit on success."""
        from bm.commands import _git_cmd

        store = tmp_path / "store"
        store.mkdir()
        # Create .git directory to simulate git repo
        (store / ".git").mkdir()

        args = MagicMock()
        args.store = str(store)

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0),  # git add
                MagicMock(returncode=0),  # git commit
                MagicMock(returncode=1),  # rev-parse -> no upstream
            ]
            cmd_sync(args)

        calls = mock_run.call_args_list
        assert len(calls) == 3
        assert calls[0][0][0] == _git_cmd("add", "-A")
        assert calls[1][0][0] == _git_cmd("commit", "-m", "bm sync", "--allow-empty")
        assert calls[2][0][0] == _git_cmd(
            "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"
        )

    def test_sync_pushes_when_upstream_exists(self, tmp_path):
        """Should push when upstream exists."""
        from bm.commands import _git_cmd

        store = tmp_path / "store"
        store.mkdir()
        (store / ".git").mkdir()

        args = MagicMock()
        args.store = str(store)

        rev_parse = _git_cmd("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        with patch("subprocess.run") as mock_run:

            def mock_return(*args, **kwargs):
                if args[0] == rev_parse:
                    return MagicMock(returncode=0)
                return MagicMock(returncode=0)

            mock_run.side_effect = mock_return
            cmd_sync(args)

        calls = mock_run.call_args_list
        assert len(calls) == 4
        assert calls[3][0][0] == _git_cmd("push")

    def test_sync_skips_push_when_no_upstream(self, tmp_path):
        """Should skip push when no upstream exists."""
        from bm.commands import _git_cmd

        store = tmp_path / "store"
        store.mkdir()
        (store / ".git").mkdir()

        args = MagicMock()
        args.store = str(store)

        rev_parse = _git_cmd("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        push = _git_cmd("push")
        with patch("subprocess.run") as mock_run:

            def mock_return(*args, **kwargs):
                if args[0] == rev_parse:
                    return MagicMock(returncode=1)
                return MagicMock(returncode=0)

            mock_run.side_effect = mock_return
            cmd_sync(args)

        calls = mock_run.call_args_list
        assert len(calls) == 3
        assert all(call[0][0] != push for call in calls)

    def test_sync_surfaces_git_failure(self, tmp_path):
        """Should exit if a git command fails."""
        from bm.commands import _git_cmd

        store = tmp_path / "store"
        store.mkdir()
        (store / ".git").mkdir()

        args = MagicMock()
        args.store = str(store)

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                returncode=5,
                cmd=_git_cmd("add", "-A"),
            )
            with pytest.raises(SystemExit) as exc_info:
                cmd_sync(args)

        assert exc_info.value.code == 5
        first_call = mock_run.call_args_list[0]
        assert first_call[0][0] == _git_cmd("add", "-A")

    def test_sync_uses_hardened_git_prefix(self, tmp_path):
        """Every git invocation should pass the hardening -c overrides."""
        store = tmp_path / "store"
        store.mkdir()
        (store / ".git").mkdir()

        args = MagicMock()
        args.store = str(store)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            cmd_sync(args)

        for call in mock_run.call_args_list:
            cmd = call[0][0]
            assert cmd[:11] == [
                "git",
                "-c",
                "core.fsmonitor=",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.sshCommand=",
                "-c",
                "credential.helper=",
                "-c",
                "protocol.file.allow=user",
            ]


class TestCmdTag:
    """Test cmd_tag function."""

    def test_tag_add_modifies_yaml_and_sorts(self, tmp_path):
        """Should add tags, modify YAML, sort and uniq."""
        store = tmp_path / "store"
        store.mkdir()
        # Create bookmark with existing tags
        content = """---
url: https://example.com
tags: [beta, alpha]
---
"""
        fpath = store / "test.bm"
        fpath.write_text(content)

        args = MagicMock()
        args.store = str(store)
        args.id = "test"
        args.action = "add"
        args.tags = ["gamma", "alpha"]  # alpha is duplicate

        cmd_tag(args)

        # Check that tags were added, sorted, and deduped
        meta, body = load_entry(fpath)
        assert meta["tags"] == ["alpha", "beta", "gamma"]

    def test_tag_remove_modifies_yaml(self, tmp_path):
        """Should remove tags and modify YAML."""
        store = tmp_path / "store"
        store.mkdir()
        # Create bookmark with existing tags
        content = """---
url: https://example.com
tags: [alpha, beta, gamma]
---
"""
        fpath = store / "test.bm"
        fpath.write_text(content)

        args = MagicMock()
        args.store = str(store)
        args.id = "test"
        args.action = "rm"
        args.tags = ["beta", "delta"]  # delta doesn't exist

        cmd_tag(args)

        # Check that beta was removed
        meta, body = load_entry(fpath)
        assert meta["tags"] == ["alpha", "gamma"]

    def test_tag_bumps_modified(self, tmp_path):
        """Should bump modified timestamp."""
        store = tmp_path / "store"
        store.mkdir()
        # Create bookmark with existing modified
        old_modified = "2023-01-15T10:00:00+00:00"
        content = f"""---
url: https://example.com
tags: [alpha]
modified: {old_modified}
---
"""
        fpath = store / "test.bm"
        fpath.write_text(content)

        args = MagicMock()
        args.store = str(store)
        args.id = "test"
        args.action = "add"
        args.tags = ["beta"]

        # Mock iso_now
        new_modified = "2023-01-16T11:00:00+00:00"
        with patch("bm.commands.iso_now", return_value=new_modified):
            cmd_tag(args)

        # Check that modified was updated
        meta, body = load_entry(fpath)
        assert meta["modified"] == new_modified
