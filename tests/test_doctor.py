"""Read-only health checks, including malformed stores and isolated Git metadata."""

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from bm.cli import main


def bookmark(store, name="a.bm", url="https://example.com", content=None):
    path = store / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        content
        if content is not None
        else f"---\nurl: {url}\ntitle: Example\n---\nNotes\n".encode()
    )
    return path


def snapshot(root):
    result = {}
    for path in root.rglob("*"):
        info = path.lstat()
        data = path.read_bytes() if stat.S_ISREG(info.st_mode) else None
        result[path.relative_to(root).as_posix()] = (info.st_mode, info.st_mtime_ns, data)
    return result


def report(store, capsys):
    status = main(["doctor", "--store", str(store), "--json"])
    captured = capsys.readouterr()
    assert captured.err == ""
    return status, json.loads(captured.out)


def test_healthy_and_empty_store(tmp_path, capsys):
    store = tmp_path / "store"
    store.mkdir()
    assert report(store, capsys) == (
        0,
        {"store": str(store), "checked": 0, "ok": True, "findings": []},
    )
    bookmark(store)
    before = snapshot(store)
    status, document = report(store, capsys)
    assert status == 0 and document["checked"] == 1
    assert snapshot(store) == before
    assert main(["--store", str(store), "doctor"]) == 0
    assert "Store healthy: 1 bookmark checked." in capsys.readouterr().out


def test_missing_store_reports_json_without_creating_it(tmp_path, capsys):
    store = tmp_path / "missing"
    status, document = report(store, capsys)
    assert status == 1 and not document["ok"]
    assert document["findings"][0]["code"] == "missing_store"
    assert not store.exists()


@pytest.mark.parametrize(
    "content, codes",
    [
        (b"---\nurl: https://example.com\n", {"invalid_front_matter", "missing_url"}),
        (b"https://example.com\nlegacy notes", {"invalid_front_matter"}),
        (b"---\nbad line\nurl: https://example.com\n---\n", {"invalid_front_matter"}),
        (
            b"---\nurl: https://example.com\nURL: https://example.org\n---\n",
            {"invalid_front_matter"},
        ),
        (b"---\ntitle: No URL\n---\n", {"missing_url"}),
        (b"---\nurl: javascript:alert(1)\n---\n", {"unsupported_url_scheme"}),
        (b"---\nurl: https://[broken\n---\n", {"invalid_url"}),
        (b"---\nurl: https:///missing-host\n---\n", {"invalid_url"}),
        (b"---\nurl: https://example.com:bad\n---\n", {"invalid_url"}),
        (b"---\nurl: mailto:\n---\n", {"invalid_url"}),
        (b"\xff", {"invalid_utf8"}),
    ],
)
def test_invalid_bookmarks_reported_without_changes(tmp_path, capsys, content, codes):
    store = tmp_path / "store"
    bookmark(store, content=content)
    before = snapshot(store)
    status, document = report(store, capsys)
    assert status == 1
    assert {finding["code"] for finding in document["findings"]} == codes
    assert all(finding["hint"] for finding in document["findings"])
    assert snapshot(store) == before


def test_unknown_metadata_and_block_scalars_are_healthy(tmp_path, capsys):
    store = tmp_path / "store"
    bookmark(
        store,
        content=(
            b"---\r\n# comment\r\nurl: https://example.com\r\ncustom: |\r\n"
            b"  freeform without colon\r\n\r\n  another line\r\ntags: [one, two]\r\n---\r\nNotes"
        ),
    )
    assert report(store, capsys)[0] == 0


def test_duplicate_ids_and_urls_are_separate_sorted_findings(tmp_path, capsys):
    store = tmp_path / "store"
    bookmark(store, "z.bm")
    bookmark(store, "a.bm")
    bookmark(store, "variant.bm", "http://www.example.com/#fragment")
    status, document = report(store, capsys)
    assert status == 1
    findings = {item["code"]: item for item in document["findings"]}
    assert findings["duplicate_id"]["paths"] == ["a.bm", "z.bm"]
    assert findings["duplicate_url"]["paths"] == ["a.bm", "variant.bm", "z.bm"]
    assert report(store, capsys)[1] == document


def test_hardlinks_and_temporary_files_are_not_removed(tmp_path, capsys):
    store = tmp_path / "store"
    source = bookmark(store)
    try:
        os.link(source, store / "moved.bm")
        os.link(source, store / "publication.tmp")
    except OSError:
        pytest.skip("hard links unavailable")
    (store / ".a.bm.abcd.edit").write_bytes(b"unfinished")
    before = snapshot(store)
    status, document = report(store, capsys)
    assert status == 1
    codes = {item["code"] for item in document["findings"]}
    assert {"possible_interrupted_move", "temporary_hardlink", "temporary_file"} <= codes
    assert snapshot(store) == before


def test_symlinks_are_reported_without_reading_targets(tmp_path, capsys):
    store = tmp_path / "store"
    bookmark(store)
    outside = tmp_path / "outside"
    bookmark(outside, "secret.bm")
    try:
        (store / "linked").symlink_to(outside, target_is_directory=True)
        (store / "linked.bm").symlink_to(outside / "secret.bm")
    except OSError:
        pytest.skip("symlinks unavailable")
    status, document = report(store, capsys)
    assert status == 1
    assert document["checked"] == 1
    codes = {item["code"] for item in document["findings"]}
    assert {"symlink_directory", "unsafe_or_unreadable_path"} <= codes
    assert not any("secret" in str(item["paths"]) for item in document["findings"])


def test_unreadable_file_is_not_silently_skipped(tmp_path, capsys):
    store = tmp_path / "store"
    target = bookmark(store)
    original = Path.read_bytes

    def deny(path):
        if path == target:
            raise PermissionError("denied")
        return original(path)

    with patch.object(Path, "read_bytes", deny):
        status, document = report(store, capsys)
    assert status == 1
    assert document["findings"][0]["code"] == "unsafe_or_unreadable_path"


def test_human_findings_include_recovery_guidance(tmp_path, capsys):
    store = tmp_path / "store"
    bookmark(store, content=b"---\ntitle: Missing URL\n---\n")
    assert main(["doctor", "--store", str(store)]) == 1
    captured = capsys.readouterr()
    assert "ERROR missing_url: 'a.bm'" in captured.out
    assert "Next:" in captured.out and "No files changed." in captured.out
    assert captured.err == ""


@pytest.fixture
def git_store(tmp_path, monkeypatch):
    if shutil.which("git") is None:
        pytest.skip("git unavailable")
    for key in list(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("HOME", str(tmp_path))
    store = tmp_path / "store"
    bookmark(store)
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(store)], check=True, capture_output=True
    )
    return store


def test_unborn_git_repository_is_healthy_and_read_only(git_store, capsys):
    before = snapshot(git_store)
    assert report(git_store, capsys)[0] == 0
    assert snapshot(git_store) == before


def test_git_lock_is_not_deleted(git_store, capsys):
    (git_store / ".git" / "index.lock").write_bytes(b"active?")
    before = snapshot(git_store)
    status, document = report(git_store, capsys)
    assert status == 1
    assert any(item["code"] == "git_lock" for item in document["findings"])
    assert snapshot(git_store) == before


def test_malformed_git_configuration_is_reported(git_store, capsys):
    (git_store / ".git" / "config").write_text("[invalid config\n")
    before = snapshot(git_store)
    status, document = report(git_store, capsys)
    assert status == 1
    assert any(item["code"] == "git_problem" for item in document["findings"])
    assert snapshot(git_store) == before


def test_git_external_commands_are_not_launched(git_store, tmp_path, capsys):
    flag = tmp_path / "must-not-exist"
    config = git_store / ".git" / "config"
    with config.open("a") as stream:
        stream.write(f'\n[core]\n\tfsmonitor = "touch {flag.as_posix()}"\n')
    assert report(git_store, capsys)[0] == 0
    assert not flag.exists()


def test_invalid_git_head_reported(git_store, capsys):
    (git_store / ".git" / "HEAD").write_text("broken\n")
    status, document = report(git_store, capsys)
    assert status == 1
    assert any(item["code"] == "git_problem" for item in document["findings"])


def test_git_unavailable_is_a_structured_finding(git_store, capsys):
    with patch("bm.commands.subprocess.run", side_effect=FileNotFoundError("git unavailable")):
        status, document = report(git_store, capsys)
    assert status == 1
    assert document["findings"][0]["code"] == "git_problem"


def test_symlink_git_marker_is_not_followed(tmp_path, capsys):
    store = tmp_path / "store"
    bookmark(store)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (store / ".git").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    with patch("bm.commands._run_git") as git:
        status, document = report(store, capsys)
    git.assert_not_called()
    assert status == 1
    assert document["findings"][0]["code"] == "git_symlink"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires FIFO support")
def test_special_bookmark_file_does_not_block_read(tmp_path, capsys):
    store = tmp_path / "store"
    store.mkdir()
    os.mkfifo(store / "pipe.bm")
    status, document = report(store, capsys)
    assert status == 1
    assert document["findings"][0]["code"] == "unsafe_or_unreadable_path"


def test_repair_is_not_an_available_option(tmp_path, capsys):
    store = tmp_path / "store"
    assert main(["doctor", "--store", str(store), "--repair"]) == 2
    assert "unrecognized arguments" in capsys.readouterr().err
    assert not store.exists()
