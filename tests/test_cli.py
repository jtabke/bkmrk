"""Unit tests for bm.cli module."""

import pytest
from unittest.mock import patch
from bm.cli import main


class TestMain:
    """Test main function."""

    @patch('bm.cli.cmd_init')
    def test_init_command(self, mock_cmd):
        """Should call cmd_init for init command."""
        with patch('sys.argv', ['bm', 'init', '--git']):
            main()
            mock_cmd.assert_called_once()

    @patch('bm.cli.cmd_add')
    def test_add_command(self, mock_cmd):
        """Should call cmd_add for add command."""
        with patch('sys.argv', ['bm', 'add', 'https://example.com']):
            main()
            mock_cmd.assert_called_once()

    def test_help(self, capsys):
        """Should show help."""
        with patch('sys.argv', ['bm', '--help']):
            with pytest.raises(SystemExit):
                main()
            captured = capsys.readouterr()
            assert 'Plain-text, pass-style bookmarks' in captured.out