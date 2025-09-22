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
