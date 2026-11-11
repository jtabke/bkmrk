"""Windows filename diagnostics without requiring Windows to test the policy."""

import sys

import pytest

from bm.cli import main
from bm.errors import UnsafePathError
from bm.paths import id_to_path, reject_windows_names


@pytest.mark.parametrize(
    "name",
    [
        "con.bm",
        "NUL.txt.bm",
        "aux/entry.bm",
        "PRN.bm",
        "com1.bm",
        "COM9.notes.bm",
        "lpt1.bm",
        "LPT9.bm",
        "COM¹.bm",
        "LPT².bm",
        "com³.bm",
        "CONIN$.bm",
        "CONOUT$.bm",
        "con .bm",
        "folder./entry.bm",
        "folder /entry.bm",
        "folder/entry.",
        "folder:stream/entry.bm",
        'folder"/entry.bm',
        "folder\x01/entry.bm",
        r"folder\NUL\entry.bm",
        r"folder\con .bm",
        r"folder.\entry.bm",
    ],
)
def test_unsupported_windows_components_have_clear_diagnostics(name):
    with pytest.raises(UnsafePathError, match="unsupported Windows filename"):
        reject_windows_names(name)


@pytest.mark.parametrize(
    "name",
    [
        "dev/example.bm",
        "conifer.bm",
        "auxiliary.bm",
        "com10.bm",
        ".notes/é.bm",
        "folder with spaces/entry.bm",
        "X.bm",
        "CONX.bm",
        "CON\u00a0.bm",
        r"dev\example.bm",
    ],
)
def test_ordinary_windows_names_are_allowed(name):
    reject_windows_names(name)


@pytest.mark.parametrize("slug", ["CON", "nul.txt", r"AUX\entry", "LPT²", "folder./entry"])
def test_windows_path_validation_runs_before_filesystem_mutation(tmp_path, monkeypatch, slug):
    monkeypatch.setattr(sys, "platform", "win32")
    root = tmp_path / "store"
    with pytest.raises(UnsafePathError, match="unsupported Windows filename"):
        id_to_path(root, slug)
    assert not root.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX filename compatibility")
def test_posix_device_like_names_remain_supported(tmp_path):
    assert id_to_path(tmp_path, "con") == tmp_path / "con.bm"
    assert id_to_path(tmp_path, "folder./entry") == tmp_path / "folder." / "entry.bm"


def test_cli_windows_filename_errors_preserve_existing_bookmarks(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "win32")
    store = tmp_path / "store"

    def run(*argv):
        return main(["--store", str(store), *argv])

    assert run("init") == 0
    capsys.readouterr()
    for arguments in [("--id", "CON"), ("--path", "AUX"), ("--path", "folder.")]:
        assert run("add", "https://example.com", *arguments) == 1
        error = capsys.readouterr().err
        assert "unsupported Windows filename" in error
        assert "Traceback" not in error
        assert not list(store.rglob("*.bm"))

    assert run("add", "https://example.com", "--id", "safe") == 0
    capsys.readouterr()
    bookmark = store / "safe.bm"
    original = bookmark.read_bytes()
    assert run("mv", "safe", "NUL") == 1
    assert "reserved device name" in capsys.readouterr().err
    assert bookmark.read_bytes() == original


def test_import_skips_windows_device_folders_and_continues(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "win32")
    source = tmp_path / "bookmarks.html"
    source.write_text(
        '<DL><DT><H3>CON</H3><DL><DT><A HREF="https://bad.example.com">Bad</A></DL>'
        '<DT><H3>Reading</H3><DL><DT><A HREF="https://good.example.com">Good</A></DL></DL>',
        encoding="utf-8",
    )
    store = tmp_path / "store"
    assert main(["--store", str(store), "import", str(source)]) == 0
    captured = capsys.readouterr()
    assert captured.out == "import ok\n"
    assert "reserved device name" in captured.err
    assert "skipped 1 entries with unsafe folder paths" in captured.err
    bookmarks = list(store.rglob("*.bm"))
    assert len(bookmarks) == 1
    assert bookmarks[0].parent.name == "reading"
