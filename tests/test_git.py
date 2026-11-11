"""Command and workflow contracts for git."""

import argparse
import subprocess
from unittest.mock import patch

import pytest

from bm.commands import (
    cmd_init,
    cmd_sync,
)
from bm.errors import BmError


class TestCmdInit:
    """Test cmd_init function."""

    def test_init_basic(self, tmp_path):
        """Should create store directory."""
        store = tmp_path / "store"
        args = argparse.Namespace()
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
        args = argparse.Namespace()
        args.store = str(store)
        args.git = False
        cmd_init(args)

        assert stat.S_IMODE(store.stat().st_mode) == 0o700
        assert stat.S_IMODE((store / "README.txt").stat().st_mode) == 0o600

    def test_init_with_git(self, tmp_path):
        """Should initialize git repo if requested."""
        from bm.git import git_command

        store = tmp_path / "store"
        args = argparse.Namespace()
        args.store = str(store)
        args.git = True

        with patch("bm.git.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(git_command("init"), 0)
            cmd_init(args)

        mock_run.assert_called_once()
        command, kwargs = mock_run.call_args
        assert command[0] == git_command("init")
        assert kwargs["cwd"] == store
        assert kwargs["check"] is True
        assert kwargs["timeout"] == 30
        assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"

    def test_init_git_failure_does_not_report_success(self, tmp_path, capsys):
        """A failed git init must not print the success message."""
        from bm.git import git_command

        store = tmp_path / "store"
        args = argparse.Namespace(store=str(store), git=True)
        error = subprocess.CalledProcessError(returncode=7, cmd=git_command("init"))

        with patch("bm.git.subprocess.run", side_effect=error), pytest.raises(BmError) as exc:
            cmd_init(args)

        assert exc.value.exit_code == 7
        assert "Initialized git repository." not in capsys.readouterr().out

    def test_init_git_timeout_is_bounded(self, tmp_path, capsys):
        """A hung git init must produce a deterministic timeout error."""
        from bm.git import git_command

        store = tmp_path / "store"
        args = argparse.Namespace(store=str(store), git=True)
        error = subprocess.TimeoutExpired(git_command("init"), timeout=30)

        with patch("bm.git.subprocess.run", side_effect=error), pytest.raises(BmError) as exc:
            cmd_init(args)

        assert exc.value.exit_code == 124
        assert "git command timed out after 30s" in str(exc.value)

    def test_init_without_git_does_not_call_git(self, tmp_path):
        """Should not call git if not requested."""
        store = tmp_path / "store"
        args = argparse.Namespace()
        args.store = str(store)
        args.git = False

        with patch("bm.git.subprocess.run") as mock_run:
            cmd_init(args)

        mock_run.assert_not_called()

    def test_init_uses_live_environment_default_store(self, tmp_path, monkeypatch):
        """The default store must be resolved from the environment at call time."""
        first_store = tmp_path / "first"
        second_store = tmp_path / "second"
        args = argparse.Namespace(store=None, git=False)

        monkeypatch.setenv("BOOKMARKS_DIR", str(first_store))
        cmd_init(args)
        monkeypatch.setenv("BOOKMARKS_DIR", str(second_store))
        cmd_init(args)

        assert first_store.is_dir()
        assert second_store.is_dir()


class TestCmdSync:
    """Test cmd_sync function."""

    def test_sync_error_when_not_git_repo(self, tmp_path):
        """Should error when store is not a git repo."""
        store = tmp_path / "store"
        store.mkdir()

        args = argparse.Namespace()
        args.store = str(store)

        with pytest.raises(BmError) as exc_info:
            cmd_sync(args)
        assert exc_info.value.exit_code == 2

    def test_sync_success_adds_and_commits(self, tmp_path):
        """Should run git add and commit on success."""
        from bm.git import git_command

        store = tmp_path / "store"
        store.mkdir()
        # Create .git directory to simulate git repo
        (store / ".git").mkdir()

        args = argparse.Namespace()
        args.store = str(store)

        with patch("bm.git.subprocess.run") as mock_run:
            mock_run.side_effect = [
                subprocess.CompletedProcess([], 0),  # git add
                subprocess.CompletedProcess([], 0),  # git commit
                subprocess.CompletedProcess([], 1),  # rev-parse -> no upstream
            ]
            cmd_sync(args)

        calls = mock_run.call_args_list
        assert len(calls) == 3
        assert calls[0][0][0] == git_command("add", "-A")
        assert calls[1][0][0] == git_command("commit", "-m", "bm sync", "--allow-empty")
        assert calls[2][0][0] == git_command(
            "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"
        )

    def test_sync_pushes_when_upstream_exists(self, tmp_path):
        """Should push when upstream exists."""
        from bm.git import git_command

        store = tmp_path / "store"
        store.mkdir()
        (store / ".git").mkdir()

        args = argparse.Namespace()
        args.store = str(store)

        rev_parse = git_command("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        with patch("bm.git.subprocess.run") as mock_run:

            def mock_return(*args, **kwargs):
                if args[0] == rev_parse:
                    return subprocess.CompletedProcess(args[0], 0)
                return subprocess.CompletedProcess(args[0], 0)

            mock_run.side_effect = mock_return
            cmd_sync(args)

        calls = mock_run.call_args_list
        assert len(calls) == 4
        assert calls[3][0][0] == git_command("push")

    def test_sync_skips_push_when_no_upstream(self, tmp_path):
        """Should skip push when no upstream exists."""
        from bm.git import git_command

        store = tmp_path / "store"
        store.mkdir()
        (store / ".git").mkdir()

        args = argparse.Namespace()
        args.store = str(store)

        rev_parse = git_command("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        push = git_command("push")
        with patch("bm.git.subprocess.run") as mock_run:

            def mock_return(*args, **kwargs):
                if args[0] == rev_parse:
                    return subprocess.CompletedProcess(args[0], 1)
                return subprocess.CompletedProcess(args[0], 0)

            mock_run.side_effect = mock_return
            cmd_sync(args)

        calls = mock_run.call_args_list
        assert len(calls) == 3
        assert all(call[0][0] != push for call in calls)

    def test_sync_surfaces_git_failure(self, tmp_path, capsys):
        """Should exit with a deterministic diagnostic if git fails."""
        from bm.git import git_command

        store = tmp_path / "store"
        store.mkdir()
        (store / ".git").mkdir()

        args = argparse.Namespace()
        args.store = str(store)

        with patch("bm.git.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                returncode=5,
                cmd=git_command("add", "-A"),
            )
            with pytest.raises(BmError) as exc_info:
                cmd_sync(args)

        assert exc_info.value.exit_code == 5
        assert "git command failed" in str(exc_info.value)
        first_call = mock_run.call_args_list[0]
        assert first_call[0][0] == git_command("add", "-A")

    def test_sync_uses_hardened_git_prefix(self, tmp_path):
        """Every git invocation should pass hardening and bounded-run options."""
        store = tmp_path / "store"
        store.mkdir()
        (store / ".git").mkdir()

        args = argparse.Namespace()
        args.store = str(store)

        with patch("bm.git.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0)
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
            assert call.kwargs["timeout"] == 30
            assert call.kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
            assert call.kwargs["check"] is (cmd[-4] != "rev-parse")

    def test_sync_timeout_is_bounded(self, tmp_path, capsys):
        """A hung sync command must stop at the configured timeout."""
        from bm.git import git_command

        store = tmp_path / "store"
        store.mkdir()
        (store / ".git").mkdir()
        args = argparse.Namespace(store=str(store))
        error = subprocess.TimeoutExpired(git_command("push"), timeout=30)

        with patch("bm.git.subprocess.run", side_effect=error), pytest.raises(BmError) as exc:
            cmd_sync(args)

        assert exc.value.exit_code == 124
        assert "git command timed out after 30s" in str(exc.value)
