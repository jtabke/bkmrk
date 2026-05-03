"""Unit tests for bm.cli module."""

from unittest.mock import patch

import pytest

from bm.cli import main


class TestMain:
    """Test main function."""

    @patch("bm.cli.cmd_init")
    def test_init_command(self, mock_cmd):
        """Should call cmd_init for init command."""
        with patch("sys.argv", ["bm", "init", "--git"]):
            main()
            mock_cmd.assert_called_once()

    @patch("bm.cli.cmd_add")
    def test_add_command(self, mock_cmd):
        """Should call cmd_add for add command."""
        with patch("sys.argv", ["bm", "add", "https://example.com"]):
            main()
            mock_cmd.assert_called_once()

    @patch("bm.cli.cmd_add")
    def test_add_command_args(self, mock_cmd):
        """Should call cmd_add with correct args."""
        with patch("sys.argv", ["bm", "add", "https://example.com", "--name", "N"]):
            main()
        mock_cmd.assert_called_once()
        args = mock_cmd.call_args[0][0]
        assert args.url == "https://example.com"
        assert args.name == "N"

    def test_help(self, capsys):
        """Should show help."""
        with patch("sys.argv", ["bm", "--help"]):
            with pytest.raises(SystemExit):
                main()
            captured = capsys.readouterr()
            assert "Plain-text, pass-style bookmarks" in captured.out

    def test_help_exit_code(self, capsys):
        """Should exit with code 0 for --help."""
        with patch("sys.argv", ["bm", "--help"]):
            with pytest.raises(SystemExit) as e:
                main()
        assert e.value.code == 0
        assert "usage:" in capsys.readouterr().out

    def test_unknown_command_exits(self, capsys):
        """Should exit with non-zero code for unknown command."""
        with patch("sys.argv", ["bm", "nope"]):
            with pytest.raises(SystemExit) as e:
                main()
        assert e.value.code != 0
        captured = capsys.readouterr()
        assert "nope" in captured.out or "nope" in captured.err

    @patch("bm.cli.cmd_list", side_effect=KeyboardInterrupt)
    def test_keyboard_interrupt_exits_130(self, _mock):
        with patch("sys.argv", ["bm", "list"]):
            with pytest.raises(SystemExit) as e:
                main()
        assert e.value.code == 130

    @patch("bm.cli.cmd_list", side_effect=BrokenPipeError)
    def test_broken_pipe_exits_zero(self, _mock):
        with patch("sys.argv", ["bm", "list"]):
            with pytest.raises(SystemExit) as e:
                main()
        assert e.value.code == 0

    @patch("bm.cli.cmd_list", side_effect=RuntimeError("boom"))
    def test_unexpected_exception_exits_2(self, _mock, capsys):
        with patch("sys.argv", ["bm", "list"]):
            with pytest.raises(SystemExit) as e:
                main()
        assert e.value.code == 2
        captured = capsys.readouterr()
        assert "RuntimeError" in captured.err
        assert "boom" in captured.err

    @patch("bm.cli.cmd_list", side_effect=RuntimeError("boom"))
    def test_bm_debug_re_raises(self, _mock):
        with patch("sys.argv", ["bm", "list"]), patch.dict(
            "os.environ", {"BM_DEBUG": "1"}, clear=False
        ):
            with pytest.raises(RuntimeError):
                main()


def test_module_entry_point_runs():
    """`python -m bm --help` should print usage cleanly."""
    import os
    import subprocess
    import sys as _sys

    # Mutmut's stats collector breaks subprocess Python tracing
    # (`max_stack_depth` AttributeError). Skip when running under mutmut.
    if "mutants" in os.getcwd():
        pytest.skip("incompatible with mutmut subprocess wrapping")

    import bm

    # Make `bm` importable in the subprocess by pointing at the package's parent dir.
    pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(bm.__file__)))
    env = os.environ.copy()
    env["PYTHONPATH"] = pkg_parent + os.pathsep + env.get("PYTHONPATH", "")

    result = subprocess.run(
        [_sys.executable, "-m", "bm", "--help"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0
    assert "usage:" in result.stdout
