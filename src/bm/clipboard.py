"""Explicit text clipboard access through optional platform utilities."""

import os
import shutil
import subprocess
import sys

from .errors import BmError

_CLIPBOARD_TIMEOUT = 3
_MAX_TEXT_BYTES = 64 * 1024


def _clipboard_command(*, write: bool) -> list[str]:
    """Choose an installed helper for the current desktop, never a shell command."""
    if sys.platform == "darwin":
        candidates = [["pbcopy" if write else "pbpaste"]]
    elif sys.platform == "win32":
        encoding = "[System.Text.UTF8Encoding]::new($false)"
        script = (
            f"[Console]::InputEncoding = {encoding}; "
            "Set-Clipboard -Value ([Console]::In.ReadToEnd())"
            if write
            else f"[Console]::OutputEncoding = {encoding}; "
            "$text = Get-Clipboard -Raw; if ($null -ne $text) { [Console]::Write($text) }"
        )
        script = "$ErrorActionPreference = 'Stop'; " + script
        candidates = [
            [program, "-NoLogo", "-NoProfile", "-NonInteractive", "-STA", "-Command", script]
            for program in ("powershell.exe", "pwsh.exe")
        ]
    else:
        candidates = []
        if os.environ.get("WAYLAND_DISPLAY"):
            candidates.append(
                ["wl-copy", "--type", "text/plain;charset=utf-8"]
                if write
                else ["wl-paste", "--no-newline", "--type", "text"]
            )
        if os.environ.get("DISPLAY"):
            candidates.extend(
                [
                    ["xclip", "-selection", "clipboard", "-i" if write else "-o"],
                    ["xsel", "--clipboard", "--input" if write else "--output"],
                ]
            )
    for command in candidates:
        executable = shutil.which(command[0])
        if executable:
            return [executable, *command[1:]]
    raise BmError(
        "clipboard unavailable: use pbcopy/pbpaste on macOS, PowerShell on Windows, "
        "or wl-clipboard (Wayland), xclip or xsel (X11) in a desktop session. "
        "For capture without a clipboard helper, use 'bm add -'.",
        exit_code=2,
    )


def _run_clipboard(*, data: bytes | None = None) -> bytes:
    """Run one helper with a timeout; do not retry or expose clipboard text in errors."""
    command = _clipboard_command(write=data is not None)
    try:
        result = subprocess.run(
            command,
            input=data,
            stdin=subprocess.DEVNULL if data is None else None,
            stdout=subprocess.PIPE if data is None else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_CLIPBOARD_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise BmError(
            "clipboard helper timed out; check your desktop session", exit_code=2
        ) from exc
    except OSError as exc:
        raise BmError(
            "could not run clipboard helper; check your installation", exit_code=2
        ) from exc
    if result.returncode:
        raise BmError(
            "clipboard helper failed; check your desktop session and clipboard access",
            exit_code=2,
        )
    return result.stdout or b""


def read_clipboard() -> str:
    """Read text only on request, rejecting oversized, NUL-containing, or non-UTF-8 data."""
    raw = _run_clipboard()
    if len(raw) > _MAX_TEXT_BYTES:
        raise BmError("clipboard text exceeds 64 KiB", exit_code=2)
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise BmError("clipboard does not contain UTF-8 text", exit_code=2) from exc
    if "\0" in text:
        raise BmError("clipboard text contains NUL bytes", exit_code=2)
    return text


def write_clipboard(text: str) -> None:
    """Replace clipboard text explicitly, passing content via stdin rather than argv."""
    try:
        raw = text.encode("utf-8")
    except UnicodeError as exc:
        raise BmError("cannot copy non-UTF-8 text", exit_code=2) from exc
    if len(raw) > _MAX_TEXT_BYTES or "\0" in text:
        raise BmError("cannot copy text larger than 64 KiB or containing NUL bytes", exit_code=2)
    _run_clipboard(data=raw)
