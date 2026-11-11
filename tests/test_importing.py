"""Command and workflow contracts for importing."""

import argparse
import io
import json
import os

import pytest

from bm.cli import main
from bm.commands import (
    cmd_import,
)
from bm.io import load_entry
from bm.utils import create_slug_from_url


def run(store, *args):
    return main(["--store", str(store), *args])


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

        args = argparse.Namespace()
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

    def test_import_no_force_race_does_not_overwrite_destination(self, tmp_path, monkeypatch):
        """A concurrent creator wins a no-force import without being overwritten."""
        import argparse

        from bm.store import Store

        store = tmp_path / "store"
        store.mkdir()
        netscape_file = tmp_path / "bookmarks.html"
        netscape_file.write_text(
            '<DL><p>\n<DT><A HREF="https://example.com">Imported</A>\n</DL><p>\n',
            encoding="utf-8",
        )
        real_create = Store.create_entry

        def interfere(current_store, path, data):
            path.write_text("raced", encoding="utf-8")
            return real_create(current_store, path, data)

        monkeypatch.setattr(Store, "create_entry", interfere)
        cmd_import(argparse.Namespace(store=str(store), file=str(netscape_file), force=False))
        assert list(store.glob("*.bm"))[0].read_text(encoding="utf-8") == "raced"

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

        args = argparse.Namespace()
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

        args = argparse.Namespace()
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

        args = argparse.Namespace()
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

        args = argparse.Namespace()
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

        args = argparse.Namespace()
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

        args = argparse.Namespace()
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

        args = argparse.Namespace()
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

        args = argparse.Namespace()
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

        args = argparse.Namespace()
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

        args = argparse.Namespace()
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


def browser_input():
    return (
        '<DL><A HREF="https://example.com/existing">Existing</A>'
        '<A HREF="https://example.com/new">New</A>'
        '<A HREF="https://example.com/new">Repeated destination</A>'
        '<A HREF="javascript:alert(1)">Unsafe</A>'
        '<A HREF="https://[broken">Broken URL</A>'
        "<A>No href</A></DL>"
    )


@pytest.mark.parametrize(
    "force, summary",
    [
        (False, "Would add 1, replace 0, skip existing 2; invalid 3."),
        (True, "Would add 1, replace 2, skip existing 0; invalid 3."),
    ],
)
def test_browser_preview_has_action_counts(
    tmp_path, monkeypatch, capsys, force, summary, bookmark_factory
):
    store = tmp_path / "store"
    existing = bookmark_factory(store, create_slug_from_url("https://example.com/existing") + ".bm")
    before = existing.read_bytes()
    monkeypatch.setattr("sys.stdin", io.StringIO(browser_input()))
    flags = ["--force"] if force else []
    assert run(store, "import", "-", "--dry-run", *flags) == 0
    captured = capsys.readouterr()
    assert captured.out == summary + "\nNo files written.\n"
    assert "skipped" in captured.err
    assert existing.read_bytes() == before
    assert list(store.iterdir()) == [existing]


def test_browser_stdin_import_retains_success_output(tmp_path, monkeypatch, capsys):
    store = tmp_path / "store"
    monkeypatch.setattr("sys.stdin", io.StringIO(browser_input()))
    assert run(store, "import", "-") == 0
    captured = capsys.readouterr()
    assert captured.out == "import ok\n"
    assert len(list(store.glob("*.bm"))) == 2


@pytest.mark.parametrize("kind", ["directory", "blocked_parent", "symlink", "symlink_parent"])
@pytest.mark.parametrize("force", [False, True])
def test_browser_preview_and_import_reject_blocked_destinations(
    tmp_path, monkeypatch, capsys, kind, force
):
    store = tmp_path / "store"
    store.mkdir()
    url = "https://example.com/blocked"
    slug = create_slug_from_url(url) + ".bm"
    folder = ""
    if kind == "directory":
        (store / slug).mkdir()
    elif kind == "blocked_parent":
        (store / "blocked").write_bytes(b"keep parent")
        folder = "blocked"
    else:
        target = store / "target"
        if kind == "symlink_parent":
            target.mkdir()
            link = store / "blocked"
            folder = "blocked"
        else:
            target.write_bytes(b"keep target")
            link = store / slug
        try:
            link.symlink_to(target, target_is_directory=kind == "symlink_parent")
        except OSError:
            pytest.skip("symlinks unavailable")
    html = f'<DL><A HREF="{url}">Blocked</A></DL>'
    if folder:
        html = f"<DL><H3>{folder}</H3>{html}</DL>"

    def state():
        return {
            path.relative_to(store).as_posix(): (
                os.readlink(path)
                if path.is_symlink()
                else path.read_bytes()
                if path.is_file()
                else None
            )
            for path in store.rglob("*")
        }

    before = state()
    for flags in (["--dry-run"], []):
        monkeypatch.setattr("sys.stdin", io.StringIO(html))
        assert run(store, "import", "-", *flags, *(["--force"] if force else [])) == 0
        captured = capsys.readouterr()
        if flags:
            assert captured.out == (
                "Would add 0, replace 0, skip existing 0; invalid 1.\nNo files written.\n"
            )
        else:
            assert captured.out == "import ok\n"
        assert "unsafe folder paths" in captured.err
        assert state() == before


@pytest.mark.parametrize("kind", ["file_root", "blocked_ancestor", "dangling_root"])
@pytest.mark.parametrize("backup", [False, True])
def test_import_preview_rejects_blocked_store_roots(tmp_path, monkeypatch, capsys, kind, backup):
    store = tmp_path / "store"
    if kind == "file_root":
        store.write_bytes(b"keep root")
    elif kind == "blocked_ancestor":
        store.write_bytes(b"keep ancestor")
        store = store / "missing-child"
    else:
        try:
            store.symlink_to(tmp_path / "missing-target", target_is_directory=True)
        except OSError:
            pytest.skip("symlinks unavailable")
    text = (
        json.dumps(
            {
                "format": "bm-backup",
                "version": 1,
                "entries": [{"path": "a.bm", "content": "---\nurl: https://example.com\n---\n"}],
            }
        )
        if backup
        else browser_input()
    )
    for flags in (["--dry-run"], []):
        monkeypatch.setattr("sys.stdin", io.StringIO(text))
        assert run(store, "import", "-", *flags) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "store path is blocked" in captured.err
    if kind == "file_root":
        assert store.read_bytes() == b"keep root"
    elif kind == "blocked_ancestor":
        assert store.parent.read_bytes() == b"keep ancestor"
    else:
        assert store.is_symlink() and not (tmp_path / "missing-target").exists()
