"""Portable regressions for Windows bookmark paths and interchange contracts."""

import json
import sys
from pathlib import PurePosixPath, PureWindowsPath
from types import SimpleNamespace

import pytest

from bm.cli import main
from bm.dedupe import _collect_body_parts, _entry_score
from bm.errors import UnsafePathError
from bm.exporting import export_row, write_netscape
from bm.netscape import parse_netscape_html
from bm.paths import id_to_path, matches_path_prefix
from bm.query import FilterSpec, _build_row
from bm.store import Store, StoreEntry


@pytest.mark.parametrize("path_type", [PurePosixPath, PureWindowsPath])
def test_logical_paths_use_forward_slashes(path_type):
    rel = path_type("dev/python/example")
    meta = {"url": "https://example.com"}

    assert matches_path_prefix(rel, "dev/python")
    assert matches_path_prefix(rel, r"dev\python")
    assert matches_path_prefix(rel, "dev")
    assert not matches_path_prefix(rel, "de")
    assert not matches_path_prefix(rel, "dev/py")
    assert matches_path_prefix(path_type("dev/python"), "dev/python")
    assert _build_row(rel, meta)["path"] == "dev/python/example"
    assert export_row(rel, meta)["path"] == "dev/python/example"


@pytest.mark.parametrize("path_type", [PurePosixPath, PureWindowsPath])
def test_netscape_export_retains_nested_folders(path_type, capsys):
    entry = SimpleNamespace(
        relative_path=path_type("dev/python/example"),
        meta={"url": "https://example.com", "title": "Example"},
    )
    store = SimpleNamespace(iter_entries=lambda **kwargs: iter([entry]))

    write_netscape(store, FilterSpec(path="dev/python"), sys.stdout)

    entries = parse_netscape_html(capsys.readouterr().out)
    assert len(entries) == 1
    assert entries[0][0] == "dev/python"
    assert entries[0][1]["url"] == "https://example.com"


@pytest.mark.parametrize("path_type", [PurePosixPath, PureWindowsPath])
def test_dedupe_uses_logical_paths_in_scores_and_notes(path_type):
    survivor = StoreEntry(
        path_type("dev/python/first.bm"), path_type("dev/python/first"), {}, "longer note"
    )
    duplicate = StoreEntry(
        path_type("dev/python/second.bm"), path_type("dev/python/second"), {}, "note"
    )

    assert _entry_score(survivor)[3] == "dev/python/first"
    parts, appended = _collect_body_parts([survivor, duplicate], survivor)
    assert appended
    assert parts == ["longer note", "[Merged from dev/python/second]\nnote"]


@pytest.mark.parametrize(
    "unsafe",
    [
        r"..\escape",
        r"dev\..\escape",
        r"dev\...\escape",
        r"C:relative",
        r"C:\absolute",
        r"\rooted",
        r"\\server\share\entry",
        "/absolute",
        "dev/entry\x00",
    ],
)
def test_store_rejects_unsafe_raw_paths_before_normalization(tmp_path, unsafe):
    store = Store(tmp_path / "store")
    with pytest.raises(UnsafePathError):
        id_to_path(store.root, unsafe)
    with pytest.raises(UnsafePathError):
        store.resolve(unsafe)
    assert not store.root.exists()


@pytest.mark.parametrize("argument", ["--path", "--id"])
@pytest.mark.parametrize("unsafe", [r"dev\..\escape", r"C:relative", "dev/entry\x00"])
def test_cli_add_rejects_unsafe_paths(tmp_path, capsys, argument, unsafe):
    store = tmp_path / "store"
    assert main(["--store", str(store), "init"]) == 0
    capsys.readouterr()

    assert main(["--store", str(store), "add", "https://example.com", argument, unsafe]) == 1
    assert capsys.readouterr().err.startswith("bm: ")
    assert not list(store.rglob("*.bm"))


def test_cli_nested_bookmark_paths_round_trip(tmp_path, capsys):
    store = tmp_path / "store"

    def run(*argv):
        return main(["--store", str(store), *argv])

    assert run("init") == 0
    capsys.readouterr()
    assert run("add", "https://example.com", "--path", r"Dev\Python", "--id", r"Docs\Example") == 0
    capsys.readouterr()
    logical_path = "dev/python/docs/example"
    bookmark = store / "dev" / "python" / "docs" / "example.bm"
    assert bookmark.is_file()

    assert run("show", r"dev\python\docs\example") == 0
    assert capsys.readouterr().out.startswith(f"# {logical_path}\n")
    for prefix in ["dev/python", r"dev\python"]:
        assert run("list", "--path", prefix, "--json") == 0
        assert json.loads(capsys.readouterr().out)[0]["path"] == logical_path
        assert run("search", "example", "--path", prefix, "--jsonl") == 0
        assert json.loads(capsys.readouterr().out)["path"] == logical_path
        assert run("export", "json", "--path", prefix) == 0
        assert json.loads(capsys.readouterr().out)[0]["path"] == logical_path
        assert run("export", "json", "--path", prefix, "--jsonl") == 0
        assert json.loads(capsys.readouterr().out)["path"] == logical_path

    assert run("export", "netscape", "--path", "dev/python") == 0
    html = capsys.readouterr().out
    assert parse_netscape_html(html)[0][0] == "dev/python/docs"
    exported = tmp_path / "bookmarks.html"
    exported.write_text(html, encoding="utf-8")
    imported = tmp_path / "imported"
    assert main(["--store", str(imported), "import", str(exported)]) == 0
    capsys.readouterr()
    assert len(list((imported / "dev" / "python" / "docs").glob("*.bm"))) == 1

    for unsafe in [r"dev\..\escape", r"C:relative", "dev/entry\x00"]:
        original = bookmark.read_bytes()
        assert run("mv", logical_path, unsafe) == 1
        capsys.readouterr()
        assert bookmark.read_bytes() == original

    assert run("mv", logical_path, r"Work\Reading\Example") == 0
    assert capsys.readouterr().out == "work/reading/example\n"
    assert not bookmark.exists()
    assert run("tag", "add", r"work\reading\example", "docs") == 0
    capsys.readouterr()
    assert run("rm", r"work\reading\example") == 0
    assert not list(store.rglob("*.bm"))
