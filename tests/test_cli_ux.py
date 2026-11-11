"""Convenience syntax must retain output, safety, and exit contracts."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from bm.cli import main


def populate(store: Path) -> None:
    store.mkdir()
    (store / "guide.bm").write_text(
        "---\nurl: https://example.com\ntitle: Python async guide\ntags: [dev]\n---\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize("query", [["python", "async"], ["python async"]])
def test_natural_search(tmp_path, capsys, query):
    store = tmp_path / "store"
    populate(store)
    assert main(["search", *query, "--store", str(store), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["path"] == "guide"


def test_quoted_regex_and_unquoted_regex_guidance(tmp_path, capsys):
    store = tmp_path / "store"
    populate(store)
    assert main(["search", "python async", "--regex", "--store", str(store)]) == 0
    capsys.readouterr()
    assert main(["search", "python", "async", "--regex", "--store", str(store)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "quote patterns" in captured.err


@pytest.mark.parametrize(
    "args",
    [
        ["--store", "STORE", "export", "json"],
        ["export", "--store", "STORE", "json"],
        ["export", "json", "--store", "STORE"],
        ["--store", "STORE", "list", "--json"],
        ["list", "--json", "--store", "STORE"],
    ],
)
def test_store_at_each_parser_level(tmp_path, monkeypatch, capsys, args):
    store = tmp_path / "store"
    populate(store)
    personal = tmp_path / "must-not-touch"
    monkeypatch.setenv("BOOKMARKS_DIR", str(personal))
    assert main([str(store) if arg == "STORE" else arg for arg in args]) == 0
    assert json.loads(capsys.readouterr().out)[0]["url"] == "https://example.com"
    assert not personal.exists()


def test_last_store_option_wins(tmp_path, capsys):
    wrong = tmp_path / "wrong"
    store = tmp_path / "store"
    populate(store)
    assert main(["--store", str(wrong), "list", "--store", str(store), "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1
    assert not wrong.exists()


@pytest.mark.parametrize("fmt", [[], ["json"], ["json", "--jsonl"], ["netscape"]])
@pytest.mark.parametrize("before_format", [False, True])
def test_safe_output_for_all_formats(tmp_path, capsys, fmt, before_format):
    store = tmp_path / "store"
    populate(store)
    base = ["--store", str(store), "export"]
    assert main([*base, *fmt]) == 0
    expected = capsys.readouterr().out.encode("utf-8")
    output = tmp_path / "out"
    flags = ["-o", str(output)]
    args = [*base, *flags, *fmt] if before_format else [*base, *fmt, *flags]
    assert main(args) == 0
    assert output.read_bytes() == expected
    assert capsys.readouterr().out == ""
    assert main(args) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "choose a new output filename" in captured.err
    assert output.read_bytes() == expected


def test_failed_export_does_not_publish_partial_output(tmp_path, capsys):
    store = tmp_path / "store"
    populate(store)
    output = tmp_path / "out"

    def fail(store, filters, stream, **kwargs):
        stream.write("incomplete")
        raise OSError("read failed")

    with patch("bm.commands.write_json", side_effect=fail):
        assert main(["export", "json", "--store", str(store), "-o", str(output)]) == 2
    assert capsys.readouterr().out == ""
    assert not output.exists()


@pytest.mark.parametrize("command", ["show", "open", "edit", "rm", "tag"])
def test_missing_entry_guidance(tmp_path, capsys, command):
    store = tmp_path / "store"
    populate(store)
    args = [command, "missing"] if command != "tag" else ["tag", "add", "missing", "dev"]
    assert main([*args, "--store", str(store)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "missing" in captured.err and "bm search" in captured.err


def test_ambiguity_and_move_conflict_guidance(tmp_path, capsys):
    store = tmp_path / "store"
    populate(store)
    (store / "other-guide.bm").write_bytes((store / "guide.bm").read_bytes())
    assert main(["show", "guid", "--store", str(store)]) == 1
    assert "Use a complete path" in capsys.readouterr().err
    assert main(["mv", "guide", "other-guide", "--store", str(store)]) == 1
    assert "choose a different path" in capsys.readouterr().err
