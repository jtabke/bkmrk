"""Private edit drafts and platform-specific editor launching without shell interpolation."""

import ntpath
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import List

from .errors import BmError
from .io import parse_for_rewrite
from .models import Metadata, metadata_text


def _split_editor_command(editor: str) -> List[str]:
    """Parse editor arguments using the host's command-line quoting rules."""
    if "\x00" in editor or not editor.strip():
        raise OSError("editor command is empty or contains a NUL byte")
    if sys.platform != "win32":
        try:
            return shlex.split(editor)
        except ValueError as exc:
            raise OSError(f"invalid editor command: {exc}") from exc

    # Windows backslashes are literal except before double quotes. shlex's
    # POSIX parser corrupts paths; its non-POSIX mode also mishandles quotes.
    import ctypes

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    parse = shell32.CommandLineToArgvW
    parse.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    parse.restype = ctypes.POINTER(ctypes.c_wchar_p)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    count = ctypes.c_int()
    argv = parse(editor.strip(), ctypes.byref(count))
    if not argv:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return list(argv[: count.value])
    finally:
        kernel32.LocalFree(argv)


def _call_windows_editor(cmd: List[str]) -> int:
    """Run native editors directly, and safely quote Windows batch launchers."""
    cmd[0] = shutil.which(cmd[0]) or cmd[0]
    if not cmd[0].lower().endswith((".cmd", ".bat")):
        return subprocess.call(cmd, shell=False)

    # Windows batch files always involve cmd.exe, even with shell=False.
    # Quote every argument, disable AutoRun/delayed expansion, and reject
    # characters that could be expanded or break quoting inside the launcher.
    if any(any(char in arg for char in '"%!\r\n\x00') for arg in cmd):
        raise OSError("batch editor arguments contain unsafe characters; use a native .exe editor")
    quoted = " ".join('"' + arg + "\\" * (len(arg) - len(arg.rstrip("\\"))) + '"' for arg in cmd)
    shell = ntpath.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
    command = f'{subprocess.list2cmdline([shell])} /d /v:off /s /c "{quoted}"'
    return subprocess.call(command, executable=shell, shell=False)


@contextmanager
def edited_bookmark(
    store_root: Path, destination: Path, initial: bytes
) -> Generator[tuple[Metadata, str], None, None]:
    """Edit a private draft; keep the editor's exact bytes if publication fails."""
    scratch = Path(tempfile.gettempdir()).resolve()
    if scratch.is_relative_to(store_root.resolve()):
        raise BmError("set TMPDIR or TEMP to a directory outside the bookmark store before editing")
    directory = Path(tempfile.mkdtemp(prefix="bm-edit-", dir=scratch))
    keep = False
    try:
        # Editors can replace files (and their modes) or leave sidecars. Keep the
        # enclosing directory private too, and close the file before launching on Windows.
        with tempfile.NamedTemporaryFile(dir=directory, suffix=".bm", delete=False) as stream:
            draft = Path(stream.name)
            stream.write(initial)
        launch_editor(draft)
        meta, body = parse_for_rewrite(draft.read_bytes(), destination)
        if not metadata_text(meta, "url"):
            raise BmError("url cleared in editor")
        try:
            yield meta, body
        except (BmError, OSError) as exc:
            keep = True
            message = f"{exc}\nEdited draft kept at: {draft}"
            if isinstance(exc, BmError):
                raise type(exc)(message, exit_code=exc.exit_code) from exc
            raise BmError(message, exit_code=2) from exc
    finally:
        if not keep:
            shutil.rmtree(directory, ignore_errors=True)


def launch_editor(path: Path) -> None:
    """Launch the editor, wait for it, and fail if it exits unsuccessfully."""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    cmd = (
        _split_editor_command(editor)
        if editor
        else ["notepad" if sys.platform == "win32" else "vi"]
    )
    if not cmd or not cmd[0]:
        raise OSError("editor command is empty")
    cmd.append(str(path))
    if sys.platform == "win32":
        returncode = _call_windows_editor(cmd)
    else:
        returncode = subprocess.call(cmd, shell=False)
    if returncode:
        raise OSError(f"editor exited with status {returncode}")
