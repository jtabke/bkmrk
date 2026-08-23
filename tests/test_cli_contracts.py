"""Compact end-to-end contracts for the public CLI boundary."""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from bm.cli import main


def _run(store: Path, *argv: str) -> int:
    """Run a command through ``main`` with a temporary store."""
    return main(["--store", str(store), *argv])


def _bookmark_path(store: Path) -> Path:
    return next(store.rglob("*.bm"))


def test_main_accepts_argv_and_preserves_parser_statuses(capsys):
    """Help and parser errors return their argparse statuses without raising."""
    assert main(["--help"]) == 0
    assert "usage:" in capsys.readouterr().out

    assert main(["not-a-command"]) == 2
    assert "not-a-command" in capsys.readouterr().err


def test_real_cli_mutation_and_json_contracts(tmp_path, capsys):
    """A representative init/add/list flow exercises parser and command wiring."""
    store = tmp_path / "store"

    assert _run(store, "init") == 0
    capsys.readouterr()
    assert (
        _run(
            store,
            "add",
            "https://example.com/docs",
            "--name",
            "Docs",
            "--tags",
            "guide,reference",
        )
        == 0
    )
    assert capsys.readouterr().out.strip()
    assert len(list(store.rglob("*.bm"))) == 1

    assert _run(store, "list", "--json") == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1
    assert rows[0]["title"] == "Docs"
    assert rows[0]["tags"] == ["guide", "reference"]

    assert _run(store, "list", "--jsonl") == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["url"] == "https://example.com/docs"


def test_real_cli_rejects_invalid_since_and_conflicting_output_modes(tmp_path, capsys):
    """Invalid filters and ambiguous output flags fail at the public CLI boundary."""
    store = tmp_path / "store"
    assert _run(store, "init") == 0
    capsys.readouterr()

    assert _run(store, "list", "--since", "not-a-date") == 2
    assert "invalid --since value" in capsys.readouterr().err

    assert _run(store, "list", "--json", "--jsonl") == 2
    assert "not allowed with argument" in capsys.readouterr().err

    assert _run(store, "search", "anything", "--json", "--jsonl") == 2
    assert "not allowed with argument" in capsys.readouterr().err


def test_real_cli_maps_invalid_path_not_found_and_no_result(tmp_path, capsys):
    """Expected command failures retain their established statuses and text."""
    store = tmp_path / "store"
    assert _run(store, "init") == 0
    capsys.readouterr()

    assert _run(store, "add", "https://example.com", "--path", "/outside") == 1
    assert capsys.readouterr().err == "bm: absolute paths not allowed\n"

    assert _run(store, "show", "missing") == 1
    assert capsys.readouterr().err == "bm: not found\n"

    assert _run(store, "search", "missing") == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_real_cli_maps_editor_and_conflict_failures(tmp_path, capsys):
    """Editor failures and optimistic conflicts are visible at the CLI boundary."""
    store = tmp_path / "store"
    assert _run(store, "init") == 0
    capsys.readouterr()
    assert _run(store, "add", "https://example.com") == 0
    capsys.readouterr()
    bookmark = _bookmark_path(store)
    token = bookmark.relative_to(store).with_suffix("").as_posix()
    original = bookmark.read_bytes()

    with patch("bm.commands._launch_editor", side_effect=OSError("editor failed")):
        assert _run(store, "edit", token) == 2
    assert capsys.readouterr().err == "bm: OSError: editor failed\n"
    assert bookmark.read_bytes() == original

    def edit_and_change_original(path: Path) -> None:
        del path
        bookmark.write_bytes(original + b"\nexternal change\n")

    with patch("bm.commands._launch_editor", side_effect=edit_and_change_original):
        assert _run(store, "edit", token) == 1
    assert "changed since it was read" in capsys.readouterr().err
    assert bookmark.read_bytes() == original + b"\nexternal change\n"


def test_module_entry_quietly_handles_downstream_pipe_close(tmp_path):
    """A real broken pipe must not become Python's shutdown status 120."""
    store = tmp_path / "store"
    store.mkdir()
    content = "---\nurl: https://example.com/{0}\ntitle: Entry {0}\n---\n"
    for index in range(1000):
        (store / f"entry-{index}.bm").write_text(content.format(index), encoding="utf-8")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    process = subprocess.Popen(
        [sys.executable, "-m", "bm", "--store", str(store), "list"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    assert process.stdout.readline()
    process.stdout.close()
    stderr = process.stderr.read()
    returncode = process.wait(timeout=10)

    assert returncode == 0
    assert stderr == ""


def test_real_cli_maps_git_failure(tmp_path, capsys):
    """Git failures from the real init command retain their subprocess status."""
    store = tmp_path / "store"
    failure = subprocess.CalledProcessError(7, ["git", "init"])

    with patch("bm.commands.subprocess.run", side_effect=failure):
        assert _run(store, "init", "--git") == 7

    captured = capsys.readouterr()
    assert "Initialized store at:" in captured.out
    assert "git command failed" in captured.err
