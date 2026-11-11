"""Clipboard helpers are optional, explicit, and never exercised against a real desktop."""

import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from bm import clipboard
from bm.errors import BmError


def desktop(monkeypatch, platform="linux", *, available=(), wayland=False, x11=False):
    monkeypatch.setattr(clipboard, "sys", SimpleNamespace(platform=platform))
    for key, enabled in (("WAYLAND_DISPLAY", wayland), ("DISPLAY", x11)):
        monkeypatch.delenv(key, raising=False)
        if enabled:
            monkeypatch.setenv(key, "test-display")
    monkeypatch.setattr(
        clipboard.shutil, "which", lambda name: f"/helpers/{name}" if name in available else None
    )


@pytest.mark.parametrize(
    "platform,available,wayland,x11,write,expected",
    [
        ("darwin", ["pbpaste"], False, False, False, ["pbpaste"]),
        ("darwin", ["pbcopy"], False, False, True, ["pbcopy"]),
        (
            "linux",
            ["wl-paste", "xclip"],
            True,
            True,
            False,
            ["wl-paste", "--no-newline", "--type", "text"],
        ),
        (
            "linux",
            ["wl-copy"],
            True,
            False,
            True,
            ["wl-copy", "--type", "text/plain;charset=utf-8"],
        ),
        ("linux", ["xclip"], False, True, False, ["xclip", "-selection", "clipboard", "-o"]),
        ("linux", ["xclip"], False, True, True, ["xclip", "-selection", "clipboard", "-i"]),
        ("linux", ["xsel"], False, True, False, ["xsel", "--clipboard", "--output"]),
        ("linux", ["xsel"], False, True, True, ["xsel", "--clipboard", "--input"]),
        ("linux", ["xclip"], True, True, False, ["xclip", "-selection", "clipboard", "-o"]),
    ],
)
def test_selects_session_appropriate_installed_helper(
    monkeypatch, platform, available, wayland, x11, write, expected
):
    desktop(monkeypatch, platform, available=available, wayland=wayland, x11=x11)
    command = clipboard._clipboard_command(write=write)
    assert command == [f"/helpers/{expected[0]}", *expected[1:]]


@pytest.mark.parametrize("write", [False, True])
@pytest.mark.parametrize("program", ["powershell.exe", "pwsh.exe"])
def test_windows_uses_fixed_utf8_powershell_script(monkeypatch, write, program):
    desktop(monkeypatch, "win32", available=[program])
    command = clipboard._clipboard_command(write=write)
    assert command[:-1] == [
        f"/helpers/{program}",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-STA",
        "-Command",
    ]
    assert "$ErrorActionPreference = 'Stop'" in command[-1]
    assert "UTF8Encoding" in command[-1]
    assert ("Set-Clipboard" if write else "Get-Clipboard -Raw") in command[-1]
    assert ("In.ReadToEnd()" if write else "[Console]::Write($text)") in command[-1]


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_missing_helpers_are_actionable_without_starting_process(monkeypatch, platform):
    desktop(monkeypatch, platform)
    process = Mock()
    monkeypatch.setattr(clipboard.subprocess, "run", process)
    with pytest.raises(BmError, match="clipboard unavailable") as error:
        clipboard.read_clipboard()
    assert error.value.exit_code == 2
    process.assert_not_called()


def test_headless_linux_does_not_launch_desktop_helper(monkeypatch):
    desktop(monkeypatch, available=["xclip", "wl-paste"])
    with pytest.raises(BmError, match="desktop session"):
        clipboard.read_clipboard()


def test_read_is_utf8_with_no_shell_and_no_stdin_consumption(monkeypatch):
    desktop(monkeypatch, "darwin", available=["pbpaste"])
    process = Mock(
        return_value=subprocess.CompletedProcess([], 0, "https://example.com/世界".encode())
    )
    monkeypatch.setattr(clipboard.subprocess, "run", process)
    assert clipboard.read_clipboard() == "https://example.com/世界"
    (command,) = process.call_args.args
    assert command == ["/helpers/pbpaste"]
    kwargs = process.call_args.kwargs
    assert not kwargs.get("shell")
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["timeout"] == 3
    assert kwargs["stderr"] == subprocess.DEVNULL


def test_copy_passes_exact_text_through_stdin_not_arguments(monkeypatch):
    desktop(monkeypatch, "win32", available=["powershell.exe"])
    text = "https://example.com/世界?x=$(touch-secret)&y='quoted'"
    process = Mock(return_value=subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(clipboard.subprocess, "run", process)
    clipboard.write_clipboard(text)
    assert text not in " ".join(process.call_args.args[0])
    kwargs = process.call_args.kwargs
    assert kwargs["input"] == text.encode("utf-8")
    assert kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL
    assert not kwargs.get("shell")


@pytest.mark.parametrize(
    "failure,message",
    [
        (FileNotFoundError("private-data"), "could not run"),
        (subprocess.TimeoutExpired("private-data", 3), "timed out"),
        (subprocess.CompletedProcess([], 1), "helper failed"),
    ],
)
def test_helper_failures_do_not_leak_data_or_retry(monkeypatch, failure, message):
    desktop(monkeypatch, "darwin", available=["pbpaste"])
    process = Mock()
    if isinstance(failure, Exception):
        process.side_effect = failure
    else:
        process.return_value = failure
    monkeypatch.setattr(clipboard.subprocess, "run", process)
    with pytest.raises(BmError, match=message) as error:
        clipboard.read_clipboard()
    assert error.value.exit_code == 2
    assert "private-data" not in str(error.value)
    process.assert_called_once()


@pytest.mark.parametrize(
    "data,message", [(b"\xff", "UTF-8"), (b"a\0b", "NUL"), (b"x" * 65537, "64 KiB")]
)
def test_invalid_clipboard_text_is_rejected(monkeypatch, data, message):
    monkeypatch.setattr(clipboard, "_run_clipboard", lambda: data)
    with pytest.raises(BmError, match=message):
        clipboard.read_clipboard()


def test_real_subprocess_read_is_isolated_from_desktop(monkeypatch):
    monkeypatch.setattr(
        clipboard,
        "_clipboard_command",
        lambda **_: [
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(b'https://example.com')",
        ],
    )
    assert clipboard.read_clipboard() == "https://example.com"


def test_real_subprocess_copy_uses_stdin_without_shell(monkeypatch, tmp_path):
    destination = tmp_path / "copied.txt"
    script = "import pathlib, sys; pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())"
    monkeypatch.setattr(
        clipboard,
        "_clipboard_command",
        lambda **_: [sys.executable, "-c", script, str(destination)],
    )
    text = "https://example.com/世界?x=$(not-a-command)&y='quoted'"
    clipboard.write_clipboard(text)
    assert destination.read_bytes() == text.encode("utf-8")


def test_real_subprocess_timeout_is_reaped(monkeypatch):
    monkeypatch.setattr(clipboard, "_CLIPBOARD_TIMEOUT", 0.05)
    monkeypatch.setattr(
        clipboard,
        "_clipboard_command",
        lambda **_: [sys.executable, "-c", "import time; time.sleep(10)"],
    )
    with pytest.raises(BmError, match="timed out"):
        clipboard.read_clipboard()


@pytest.mark.parametrize("text", ["x" * 65537, "a\0b", "\ud800"])
def test_invalid_copy_text_never_invokes_helper(monkeypatch, text):
    process = Mock()
    monkeypatch.setattr(clipboard, "_run_clipboard", process)
    with pytest.raises(BmError):
        clipboard.write_clipboard(text)
    process.assert_not_called()
