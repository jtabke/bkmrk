"""Unit tests for bm.commands module."""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from bm.commands import cmd_init, cmd_add, resolve_id_or_path, find_candidates


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

    def test_init_with_git(self, tmp_path):
        """Should initialize git repo if requested."""
        store = tmp_path / "store"
        args = MagicMock()
        args.store = str(store)
        args.git = True

        with patch('subprocess.run') as mock_run:
            cmd_init(args)

        mock_run.assert_called_once_with(["git", "init"], cwd=store)

    def test_init_without_git_does_not_call_git(self, tmp_path):
        """Should not call git if not requested."""
        store = tmp_path / "store"
        args = MagicMock()
        args.store = str(store)
        args.git = False

        with patch('subprocess.run') as mock_run:
            cmd_init(args)

        mock_run.assert_not_called()


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

        with patch('bm.commands._launch_editor'):
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

        with patch('bm.commands._launch_editor'):
            cmd_add(args)

        # Second add without force should fail
        with patch('bm.commands._launch_editor'):
            with pytest.raises(SystemExit):
                cmd_add(args)

        args.force = True
        with patch('bm.commands._launch_editor'):
            cmd_add(args)  # Should succeed


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


class TestFindCandidates:
    """Test find_candidates function."""

    def test_exact_match(self, tmp_path):
        """Should find exact match."""
        store = tmp_path / "store"
        store.mkdir()
        fpath = store / "test.bm"
        fpath.write_text("content")

        result = find_candidates(store, "test")
        assert result == [fpath]

    def test_fuzzy_match(self, tmp_path):
        """Should find fuzzy match."""
        store = tmp_path / "store"
        store.mkdir()
        fpath = store / "example-test-abc123.bm"
        fpath.write_text("content")

        result = find_candidates(store, "test")
        assert result == [fpath]

    def test_find_candidates_none(self, tmp_path):
        """Should return empty list for no matches."""
        store = tmp_path / "store"
        store.mkdir()
        assert find_candidates(store, "nope") == []