"""Command and workflow contracts for capture."""

import argparse
from unittest.mock import patch

import pytest

from bm.commands import (
    cmd_add,
)
from bm.errors import BmError, UnsafePathError


class TestCmdAdd:
    """Test cmd_add function."""

    def test_add_basic(self, tmp_path):
        """Should create bookmark file."""
        store = tmp_path / "store"
        store.mkdir()
        args = argparse.Namespace()
        args.store = str(store)
        args.url = "https://example.com"
        args.id = None
        args.path = None
        args.name = "Example"
        args.tags = "tag1,tag2"
        args.description = "Notes"
        args.force = False
        args.edit = False

        with patch("bm.commands.launch_editor"):
            cmd_add(args)

        # Should create file
        files = list(store.glob("*.bm"))
        assert len(files) == 1
        fpath = files[0]
        content = fpath.read_text()
        assert "url: https://example.com" in content
        assert "title: Example" in content
        assert "tags: [tag1, tag2]" in content

    def test_add_no_force_race_does_not_overwrite_destination(self, tmp_path, monkeypatch):
        """A concurrent creator wins a no-force add without being overwritten."""
        import argparse

        from bm.store import Store

        store = tmp_path / "store"
        store.mkdir()
        args = argparse.Namespace(
            store=str(store),
            url="https://example.com",
            id="entry",
            path=None,
            name="New",
            tags=None,
            description=None,
            force=False,
            edit=False,
        )
        real_create = Store.create_entry

        def interfere(current_store, path, data):
            path.write_text("raced", encoding="utf-8")
            return real_create(current_store, path, data)

        monkeypatch.setattr(Store, "create_entry", interfere)
        with pytest.raises(BmError) as exc_info:
            cmd_add(args)
        assert "bookmark exists" in str(exc_info.value)
        assert (store / "entry.bm").read_text(encoding="utf-8") == "raced"

    def test_add_generated_slug_excludes_url_userinfo(self, tmp_path):
        """Auto-generated filenames must not leak URL credentials/userinfo."""
        store = tmp_path / "store"
        store.mkdir()
        args = argparse.Namespace()
        args.store = str(store)
        args.url = "https://user:pass@example.com/path"
        args.id = None
        args.path = None
        args.name = None
        args.tags = None
        args.description = None
        args.force = False
        args.edit = False

        cmd_add(args)

        files = list(store.glob("*.bm"))
        assert len(files) == 1
        assert files[0].name.startswith("example-com-path-")
        assert "user" not in files[0].name
        assert "pass" not in files[0].name

    def test_add_force_overwrite(self, tmp_path):
        """Should overwrite with --force."""
        store = tmp_path / "store"
        store.mkdir()
        args = argparse.Namespace()
        args.store = str(store)
        args.url = "https://example.com"
        args.id = None
        args.path = None
        args.name = "Example"
        args.tags = "tag1,tag2"
        args.description = "Notes"
        args.force = False
        args.edit = False

        with patch("bm.commands.launch_editor"):
            cmd_add(args)

        # Second add without force should fail
        with patch("bm.commands.launch_editor"):
            with pytest.raises(BmError):
                cmd_add(args)

        args.force = True
        with patch("bm.commands.launch_editor"):
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

        with patch("bm.commands.launch_editor", side_effect=fake_editor):
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

        with patch("bm.commands.launch_editor", side_effect=change_url):
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

        with patch("bm.commands.launch_editor", side_effect=clear_url):
            with pytest.raises(BmError):
                cmd_add(args)

    def test_add_rejects_absolute_folder_before_normalizing(self, tmp_path):
        """An absolute --path must not be silently converted to a slug."""
        import argparse

        store = tmp_path / "store"
        store.mkdir()
        args = argparse.Namespace(
            store=str(store),
            url="https://example.com",
            id=None,
            path="/outside",
            name=None,
            tags=None,
            description=None,
            force=False,
            edit=False,
        )

        with pytest.raises(UnsafePathError):
            cmd_add(args)
        assert not list(store.glob("*.bm"))
