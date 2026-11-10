"""Editor command parsing and real-process save-before-return contracts."""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from bm.cli import main
from bm.io import load_entry
from bm.utils import _call_windows_editor, _launch_editor, _split_editor_command


@pytest.mark.skipif(sys.platform != "win32", reason="uses the Windows command-line parser")
@pytest.mark.parametrize(
    "arguments",
    [
        [r"C:\Tools\editor.exe", "--wait"],
        [r"C:\Program Files\Editor\editor.exe", "--wait"],
        ["code.cmd", "--wait", '--label=a "quoted" label', ""],
        ["editor.exe", "--directory", "C:\\Folder With Spaces\\"],
    ],
)
def test_windows_editor_arguments_round_trip(arguments):
    assert _split_editor_command(subprocess.list2cmdline(arguments)) == arguments


@pytest.mark.parametrize("command", [" ", '""'])
def test_empty_or_nul_editor_commands_fail(tmp_path, monkeypatch, command):
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", command)
    call = Mock()
    monkeypatch.setattr("bm.utils.subprocess.call", call)
    with pytest.raises(OSError, match="empty|NUL"):
        _launch_editor(tmp_path / "entry.bm")
    call.assert_not_called()


def test_editor_parser_rejects_nul():
    with pytest.raises(OSError, match="NUL"):
        _split_editor_command("editor\x00ignored")


@pytest.mark.parametrize("platform,expected", [("linux", "vi"), ("win32", "notepad")])
def test_default_editor_is_native_and_waited_for(tmp_path, monkeypatch, platform, expected):
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr("bm.utils.shutil.which", lambda name: None)
    call = Mock(return_value=0)
    monkeypatch.setattr("bm.utils.subprocess.call", call)
    path = tmp_path / "entry.bm"

    _launch_editor(path)

    call.assert_called_once_with([expected, str(path)], shell=False)


def test_visual_takes_precedence_over_editor(tmp_path, monkeypatch):
    monkeypatch.setenv("VISUAL", "visual-editor --wait")
    monkeypatch.setenv("EDITOR", "other-editor")
    monkeypatch.setattr("bm.utils.shutil.which", lambda name: None)
    call = Mock(return_value=0)
    monkeypatch.setattr("bm.utils.subprocess.call", call)
    path = tmp_path / "entry.bm"
    _launch_editor(path)
    call.assert_called_once_with(["visual-editor", "--wait", str(path)], shell=False)


@pytest.mark.parametrize("unsafe", ['quote"', "%PATH%", "!variable!", "line\nbreak", "line\rbreak"])
def test_batch_editor_refuses_expandable_or_unquotable_arguments(monkeypatch, unsafe):
    monkeypatch.setattr("bm.utils.shutil.which", lambda name: None)
    call = Mock()
    monkeypatch.setattr("bm.utils.subprocess.call", call)
    with pytest.raises(OSError, match="use a native .exe editor"):
        _call_windows_editor(["code.cmd", "--wait", unsafe])
    call.assert_not_called()


def _editor_script(directory: Path, *, fail: bool = False) -> Path:
    script = directory / "fake editor.py"
    script.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "assert sys.argv[1:-1] == ['--wait']\n"
        "path = Path(sys.argv[-1])\n"
        "text = path.read_text(encoding='utf-8')\n"
        "path.write_text(text + '\\nsaved by child editor\\n', encoding='utf-8')\n"
        f"sys.exit({7 if fail else 0})\n",
        encoding="utf-8",
    )
    return script


def _configure_editor(monkeypatch, directory: Path, script: Path, batch: bool) -> None:
    monkeypatch.delenv("VISUAL", raising=False)
    if batch:
        launcher = directory / "code.cmd"
        launcher.write_text(
            f'@echo off\n"{sys.executable}" "{script}" %*\nexit /b %errorlevel%\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("PATH", str(directory) + os.pathsep + os.environ.get("PATH", ""))
        monkeypatch.setenv("EDITOR", "code.cmd --wait")
    else:
        arguments = [sys.executable, str(script), "--wait"]
        if sys.platform == "win32":
            command = subprocess.list2cmdline(arguments)
        else:
            import shlex

            command = " ".join(shlex.quote(arg) for arg in arguments)
        monkeypatch.setenv("EDITOR", command)


@pytest.mark.parametrize(
    "batch",
    [
        False,
        pytest.param(
            True, marks=pytest.mark.skipif(sys.platform != "win32", reason="Windows batch launcher")
        ),
    ],
)
@pytest.mark.parametrize("fail", [False, True])
def test_real_editor_save_and_failure_contracts(tmp_path, monkeypatch, capsys, batch, fail):
    directory = tmp_path / "Editors & Tools (test)"
    directory.mkdir()
    script = _editor_script(directory, fail=fail)
    _configure_editor(monkeypatch, directory, script, batch)
    store = tmp_path / "bookmarks & Unicode é (test)"

    def run(*argv):
        return main(["--store", str(store), *argv])

    assert run("init") == 0
    capsys.readouterr()
    assert run("add", "https://example.com", "--id", "reading/example", "--edit") == (
        2 if fail else 0
    )
    result = capsys.readouterr()
    bookmark = store / "reading" / "example.bm"
    if fail:
        assert "editor exited with status 7" in result.err
        assert not bookmark.exists()
        assert run("add", "https://example.com", "--id", "reading/example") == 0
        capsys.readouterr()
    else:
        assert "saved by child editor" in load_entry(bookmark)[1]
    original = bookmark.read_bytes()

    assert run("edit", "reading/example") == (2 if fail else 0)
    result = capsys.readouterr()
    if fail:
        assert "editor exited with status 7" in result.err
        assert bookmark.read_bytes() == original
    else:
        assert load_entry(bookmark)[1].count("saved by child editor") == 2
    assert not list(bookmark.parent.glob(".*.edit"))
