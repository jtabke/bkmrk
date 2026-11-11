"""Unit tests for bm.commands module."""

import argparse
from unittest.mock import patch

import pytest

from bm.commands import (
    cmd_edit,
    cmd_mv,
    cmd_open,
    cmd_rm,
    cmd_show,
    cmd_tag,
    cmd_tags,
)
from bm.errors import BmError, ConflictError, NotFoundError, UnsafePathError
from bm.io import load_entry


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

        args = argparse.Namespace()
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

        args = argparse.Namespace()
        args.store = str(store)
        args.id = "test"

        with pytest.raises(BmError):
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

        args = argparse.Namespace()
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

        with patch("webbrowser.open") as mock_open, pytest.raises(BmError) as exc:
            cmd_open(args)
        mock_open.assert_not_called()
        assert "refusing to open 'javascript' URL" in str(exc.value)

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

        args = argparse.Namespace()
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

        args = argparse.Namespace()
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

        args = argparse.Namespace()
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

        args = argparse.Namespace()
        args.store = str(store)
        args.id = "test"

        # Mock the editor to do nothing
        with patch("bm.commands.launch_editor"):
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

        args = argparse.Namespace()
        args.store = str(store)
        args.id = "test"

        # Mock the editor to do nothing
        with patch("bm.commands.launch_editor"):
            # Mock iso_now to return a specific timestamp
            new_modified = "2023-01-16T11:00:00+00:00"
            with patch("bm.commands.iso_now", return_value=new_modified):
                cmd_edit(args)

        # Check that modified was added
        meta, body = load_entry(fpath)
        assert meta["modified"] == new_modified

    def test_edit_uses_temporary_copy_and_preserves_unknown_fields(self, tmp_path):
        """Editor changes should be staged in a temp copy and retain custom metadata."""
        store = tmp_path / "store"
        store.mkdir()
        fpath = store / "test.bm"
        fpath.write_text(
            "---\nurl: https://example.com\ntitle: Original\ncustom: retained\n---\nbody\n"
        )
        edited_paths = []

        def fake_editor(path):
            edited_paths.append(path)
            assert path != fpath
            path.write_text(
                "---\nurl: https://example.com\ntitle: Edited\ncustom: retained\n---\nchanged\n"
            )

        args = argparse.Namespace(store=str(store), id="test")
        with patch("bm.commands.launch_editor", side_effect=fake_editor):
            with patch("bm.commands.iso_now", return_value="2024-01-01T00:00:00+00:00"):
                cmd_edit(args)

        meta, body = load_entry(fpath)
        assert meta["title"] == "Edited"
        assert meta["custom"] == "retained"
        assert body == "changed\n"
        assert edited_paths and not edited_paths[0].exists()

    def test_edit_failure_leaves_original_untouched(self, tmp_path):
        """A failed editor must not alter the live bookmark."""
        store = tmp_path / "store"
        store.mkdir()
        fpath = store / "test.bm"
        original = "---\nurl: https://example.com\ntitle: Original\n---\nbody\n"
        fpath.write_text(original)

        args = argparse.Namespace(store=str(store), id="test")
        with patch("bm.commands.launch_editor", side_effect=OSError("editor failed")):
            with pytest.raises(OSError, match="editor failed"):
                cmd_edit(args)

        assert fpath.read_text() == original
        assert not list(store.glob(".*.edit"))

    def test_edit_invalid_result_leaves_original_untouched(self, tmp_path):
        """An edited entry without a URL must be rejected before commit."""
        store = tmp_path / "store"
        store.mkdir()
        fpath = store / "test.bm"
        original = "---\nurl: https://example.com\ntitle: Original\n---\nbody\n"
        fpath.write_text(original)

        def clear_url(path):
            path.write_text("---\ntitle: Missing URL\n---\n")

        args = argparse.Namespace(store=str(store), id="test")
        with patch("bm.commands.launch_editor", side_effect=clear_url):
            with pytest.raises(BmError):
                cmd_edit(args)

        assert fpath.read_text() == original

    def test_edit_rejects_stale_original(self, tmp_path):
        """A concurrent edit must not be overwritten by the editor result."""
        store = tmp_path / "store"
        store.mkdir()
        fpath = store / "test.bm"
        original = "---\nurl: https://example.com\ntitle: Original\n---\nbody\n"
        fpath.write_text(original)

        def concurrent_editor(path):
            path.write_text(original)
            fpath.write_text("external change\n")

        args = argparse.Namespace(store=str(store), id="test")
        with patch("bm.commands.launch_editor", side_effect=concurrent_editor):
            with pytest.raises(ConflictError):
                cmd_edit(args)

        assert fpath.read_text() == "external change\n"


class TestCmdRm:
    """Test cmd_rm function."""

    def test_rm_removes_file(self, tmp_path):
        """Should remove the bookmark file."""
        store = tmp_path / "store"
        store.mkdir()
        fpath = store / "test.bm"
        fpath.write_text("content")

        args = argparse.Namespace()
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

        args = argparse.Namespace()
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

        args = argparse.Namespace()
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

        args = argparse.Namespace()
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

        args = argparse.Namespace()
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

        args = argparse.Namespace()
        args.store = str(store)
        args.src = "old"
        args.dst = "existing"
        args.force = False

        with pytest.raises(BmError):
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

        args = argparse.Namespace()
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

        args = argparse.Namespace()
        args.store = str(store)
        args.src = "old"
        args.dst = "../../../outside"
        args.force = False

        with pytest.raises(UnsafePathError):
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

        args = argparse.Namespace()
        args.store = str(store)
        args.src = "linked"
        args.dst = "elsewhere"
        args.force = False

        with pytest.raises(UnsafePathError):
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

        args = argparse.Namespace()
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

        args = argparse.Namespace()
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

        args = argparse.Namespace()
        args.store = str(store)

        cmd_tags(args)
        captured = capsys.readouterr()
        output_lines = captured.out.strip().split("\n")

        # Should be sorted and deduped: alpha, zebra
        expected_tags = ["alpha", "zebra"]
        assert output_lines == expected_tags


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


class TestNotFoundErrors:
    """Each lookup command should raise a domain error for an unknown id."""

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
    def test_unknown_id_raises_domain_error(self, store, args_factory, ctor, extra):
        import bm.commands as cmds

        fn = getattr(cmds, f"cmd_{ctor}")
        args = args_factory(id="ghost", **extra)
        with pytest.raises(NotFoundError):
            fn(args)


class TestStoreMissingErrors:
    """Commands that explicitly check for the store should raise when it's gone."""

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
        with pytest.raises(NotFoundError):
            getattr(cmds, f"cmd_{name}")(args)


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

        args = argparse.Namespace()
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

        args = argparse.Namespace()
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

        args = argparse.Namespace()
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

    def test_tag_rejects_stale_original(self, tmp_path, monkeypatch):
        """Tag updates must not overwrite a file changed after it was read."""
        from bm.store import Store

        real_write = Store.write

        store = tmp_path / "store"
        store.mkdir()
        fpath = store / "test.bm"
        fpath.write_text("---\nurl: https://example.com\ntags: [alpha]\n---\n")

        def stale_write(store, path, data, expected=None):
            path.write_text("external change\n")
            return real_write(store, path, data, expected=expected)

        monkeypatch.setattr(Store, "write", stale_write)
        args = argparse.Namespace(store=str(store), id="test", action="add", tags=["beta"])

        with pytest.raises(ConflictError):
            cmd_tag(args)

        assert fpath.read_text() == "external change\n"
