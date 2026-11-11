"""Interspersed search, explicit stdin, import previews, and optional fzf selection."""

import io
import json
import os
import shutil
import subprocess
from unittest.mock import patch

import pytest

from bm.cli import main
from bm.io import load_entry
from bm.utils import create_slug_from_url, rid


def run(store, *args):
    return main(["--store", str(store), *args])


def bookmark(store, name="guide.bm", url="https://example.com", title="Python async guide"):
    path = store / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nurl: {url}\ntitle: {title}\ntags: [dev]\ncreated: 2025-01-01\n---\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    "args",
    [
        ["python", "--tag", "dev", "async", "--json"],
        ["--tag", "dev", "python", "--json", "async"],
        ["python", "--field", "title", "async", "--host", "example.com", "guide", "--json"],
    ],
)
def test_interspersed_search(tmp_path, capsys, args):
    store = tmp_path / "store"
    bookmark(store)
    assert run(store, "search", *args) == 0
    assert json.loads(capsys.readouterr().out)[0]["path"] == "guide"


def test_store_between_search_terms_and_last_store_wins(tmp_path, capsys):
    store = tmp_path / "store"
    bookmark(store)
    assert (
        run(tmp_path / "wrong", "search", "python", "--store", str(store), "async", "--json") == 0
    )
    assert len(json.loads(capsys.readouterr().out)) == 1


def test_search_unknown_options_and_interspersed_regex_remain_errors(tmp_path, capsys):
    store = tmp_path / "store"
    bookmark(store)
    assert run(store, "search", "python", "--bogus", "async") == 2
    assert "unrecognized arguments" in capsys.readouterr().err
    assert run(store, "search", "python", "--regex", "async") == 2
    assert "quote patterns" in capsys.readouterr().err
    assert run(store, "list", "extra") == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_search_double_dash_after_options_retains_literal_terms(tmp_path, capsys):
    store = tmp_path / "store"
    bookmark(store, title="python --literal")
    assert run(store, "search", "python", "--tag", "dev", "--", "--literal") == 0
    assert "guide" in capsys.readouterr().out


def test_add_explicit_stdin_url(tmp_path, monkeypatch, capsys):
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setattr("sys.stdin", io.StringIO("  https://example.com/日本語\n"))
    assert run(store, "add", "-", "--name", "Example", "--tags", "dev") == 0
    assert capsys.readouterr().out.strip() == rid("https://example.com/日本語")
    meta, _ = load_entry(next(store.glob("*.bm")))
    assert meta["url"] == "https://example.com/日本語"
    assert meta["title"] == "Example" and meta["tags"] == ["dev"]


@pytest.mark.parametrize(
    "text", ["", "\n  ", "https://a.com\nhttps://b.com\n", "https://a.com has spaces"]
)
def test_invalid_stdin_url_does_not_create_bookmark(tmp_path, monkeypatch, capsys, text):
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    assert run(store, "add", "-") == 1
    assert not list(store.iterdir())
    assert "exactly one" in capsys.readouterr().err


def test_ordinary_add_does_not_read_stdin(tmp_path, capsys):
    store = tmp_path / "store"
    store.mkdir()
    # pytest's default stdin raises if read.
    assert run(store, "add", "https://example.com") == 0
    assert capsys.readouterr().out.strip()


def test_stdin_backup_preview_and_restore_preserve_bytes(tmp_path, monkeypatch, capsys):
    original = tmp_path / "original"
    target = bookmark(original)
    target.write_bytes(target.read_bytes().replace(b"\n", b"\r\n") + b"\r\nNotes  \r\n")
    assert run(original, "export") == 0
    backup = capsys.readouterr().out.encode()
    restored = tmp_path / "restored"
    for flags in (("--dry-run",), ()):
        stream = io.TextIOWrapper(io.BytesIO(backup), encoding="utf-8")
        monkeypatch.setattr("sys.stdin", stream)
        assert run(restored, "import", "-", *flags) == 0
        if flags:
            assert not restored.exists()
        capsys.readouterr()
    assert (restored / "guide.bm").read_bytes() == target.read_bytes()


@pytest.mark.parametrize(
    "raw", [b"", b"  \n", b"{bad", b"[]", b'{"format":"bm-backup", "entries":[], "version":2}']
)
def test_invalid_stdin_backup_does_not_create_store(tmp_path, monkeypatch, raw):
    store = tmp_path / "store"
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8"))
    assert run(store, "import", "-") == 1
    assert not store.exists()


def browser_input():
    return (
        '<DL><A HREF="https://example.com/existing">Existing</A>'
        '<A HREF="https://example.com/new">New</A>'
        '<A HREF="https://example.com/new">Repeated destination</A>'
        '<A HREF="javascript:alert(1)">Unsafe</A>'
        '<A HREF="https://[broken">Broken URL</A>'
        "<A>No href</A></DL>"
    )


@pytest.mark.parametrize(
    "force, summary",
    [
        (False, "Would add 1, replace 0, skip existing 2; invalid 3."),
        (True, "Would add 1, replace 2, skip existing 0; invalid 3."),
    ],
)
def test_browser_preview_has_action_counts(tmp_path, monkeypatch, capsys, force, summary):
    store = tmp_path / "store"
    existing = bookmark(store, create_slug_from_url("https://example.com/existing") + ".bm")
    before = existing.read_bytes()
    monkeypatch.setattr("sys.stdin", io.StringIO(browser_input()))
    flags = ["--force"] if force else []
    assert run(store, "import", "-", "--dry-run", *flags) == 0
    captured = capsys.readouterr()
    assert captured.out == summary + "\nNo files written.\n"
    assert "skipped" in captured.err
    assert existing.read_bytes() == before
    assert list(store.iterdir()) == [existing]


def test_browser_stdin_import_retains_success_output(tmp_path, monkeypatch, capsys):
    store = tmp_path / "store"
    monkeypatch.setattr("sys.stdin", io.StringIO(browser_input()))
    assert run(store, "import", "-") == 0
    captured = capsys.readouterr()
    assert captured.out == "import ok\n"
    assert len(list(store.glob("*.bm"))) == 2


@pytest.mark.parametrize("kind", ["directory", "blocked_parent", "symlink", "symlink_parent"])
@pytest.mark.parametrize("force", [False, True])
def test_browser_preview_and_import_reject_blocked_destinations(
    tmp_path, monkeypatch, capsys, kind, force
):
    store = tmp_path / "store"
    store.mkdir()
    url = "https://example.com/blocked"
    slug = create_slug_from_url(url) + ".bm"
    folder = ""
    if kind == "directory":
        (store / slug).mkdir()
    elif kind == "blocked_parent":
        (store / "blocked").write_bytes(b"keep parent")
        folder = "blocked"
    else:
        target = store / "target"
        if kind == "symlink_parent":
            target.mkdir()
            link = store / "blocked"
            folder = "blocked"
        else:
            target.write_bytes(b"keep target")
            link = store / slug
        try:
            link.symlink_to(target, target_is_directory=kind == "symlink_parent")
        except OSError:
            pytest.skip("symlinks unavailable")
    html = f'<DL><A HREF="{url}">Blocked</A></DL>'
    if folder:
        html = f"<DL><H3>{folder}</H3>{html}</DL>"

    def state():
        return {
            path.relative_to(store).as_posix(): (
                os.readlink(path)
                if path.is_symlink()
                else path.read_bytes()
                if path.is_file()
                else None
            )
            for path in store.rglob("*")
        }

    before = state()
    for flags in (["--dry-run"], []):
        monkeypatch.setattr("sys.stdin", io.StringIO(html))
        assert run(store, "import", "-", *flags, *(["--force"] if force else [])) == 0
        captured = capsys.readouterr()
        if flags:
            assert captured.out == (
                "Would add 0, replace 0, skip existing 0; invalid 1.\nNo files written.\n"
            )
        else:
            assert captured.out == "import ok\n"
        assert "unsafe folder paths" in captured.err
        assert state() == before


@pytest.mark.parametrize("kind", ["file_root", "blocked_ancestor", "dangling_root"])
@pytest.mark.parametrize("backup", [False, True])
def test_import_preview_rejects_blocked_store_roots(tmp_path, monkeypatch, capsys, kind, backup):
    store = tmp_path / "store"
    if kind == "file_root":
        store.write_bytes(b"keep root")
    elif kind == "blocked_ancestor":
        store.write_bytes(b"keep ancestor")
        store = store / "missing-child"
    else:
        try:
            store.symlink_to(tmp_path / "missing-target", target_is_directory=True)
        except OSError:
            pytest.skip("symlinks unavailable")
    text = (
        json.dumps(
            {
                "format": "bm-backup",
                "version": 1,
                "entries": [{"path": "a.bm", "content": "---\nurl: https://example.com\n---\n"}],
            }
        )
        if backup
        else browser_input()
    )
    for flags in (["--dry-run"], []):
        monkeypatch.setattr("sys.stdin", io.StringIO(text))
        assert run(store, "import", "-", *flags) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "store path is blocked" in captured.err
    if kind == "file_root":
        assert store.read_bytes() == b"keep root"
    elif kind == "blocked_ancestor":
        assert store.parent.read_bytes() == b"keep ancestor"
    else:
        assert store.is_symlink() and not (tmp_path / "missing-target").exists()


def fzf_choose(command, **kwargs):
    assert command[0] == "fzf"
    return subprocess.CompletedProcess(command, 0, kwargs["input"].splitlines()[0] + "\n")


@pytest.mark.parametrize("field", ["id", "url"])
def test_pick_prints_without_opening_browser(tmp_path, capsys, field):
    store = tmp_path / "store"
    bookmark(store)
    with (
        patch("bm.commands.subprocess.run", side_effect=fzf_choose),
        patch("bm.commands.webbrowser.open") as browser,
    ):
        assert run(store, "pick", "--print", field) == 0
    browser.assert_not_called()
    expected = rid("https://example.com") if field == "id" else "https://example.com"
    assert capsys.readouterr().out == expected + "\n"


def test_pick_opens_selected_url_and_does_not_mutate_store(tmp_path, capsys):
    store = tmp_path / "store"
    path = bookmark(store)
    before = path.read_bytes()
    with (
        patch("bm.commands.subprocess.run", side_effect=fzf_choose),
        patch("bm.commands.webbrowser.open", return_value=True) as browser,
    ):
        assert run(store, "pick") == 0
    browser.assert_called_once_with("https://example.com")
    assert capsys.readouterr().out == "https://example.com\n"
    assert path.read_bytes() == before


def test_pick_filters_and_sanitizes_untrusted_display(tmp_path, monkeypatch, capsys):
    store = tmp_path / "store"
    bookmark(store, "dev/guide.bm", title="Python\x1b[31m")
    bookmark(store, "other/skip.bm", url="https://other.test")
    monkeypatch.setenv("FZF_DEFAULT_OPTS", "--bind=start:execute(touch should-not-run)")
    monkeypatch.setenv("FZF_DEFAULT_OPTS_FILE", "/should-not-read")

    def choose(command, **kwargs):
        assert "\x1b" not in kwargs["input"]
        assert "skip" not in kwargs["input"]
        assert "FZF_DEFAULT_OPTS" not in kwargs["env"]
        assert "FZF_DEFAULT_OPTS_FILE" not in kwargs["env"]
        assert "shell" not in kwargs
        return fzf_choose(command, **kwargs)

    with patch("bm.commands.subprocess.run", side_effect=choose):
        assert (
            run(
                store,
                "pick",
                "--path",
                "dev",
                "--host",
                "example.com",
                "--tag",
                "dev",
                "--since",
                "2024-01-01",
                "--print",
                "url",
            )
            == 0
        )
    assert capsys.readouterr().out == "https://example.com\n"


def test_pick_missing_fzf_is_actionable(tmp_path, capsys):
    store = tmp_path / "store"
    bookmark(store)
    with patch("bm.commands.subprocess.run", side_effect=FileNotFoundError("fzf")):
        assert run(store, "pick", "--print", "url") == 2
    assert "install fzf" in capsys.readouterr().err


@pytest.mark.parametrize("status", [1, 130])
def test_pick_cancellation_is_quiet_and_opens_nothing(tmp_path, capsys, status):
    store = tmp_path / "store"
    bookmark(store)
    result = subprocess.CompletedProcess(["fzf"], status, "")
    with (
        patch("bm.commands.subprocess.run", return_value=result),
        patch("bm.commands.webbrowser.open") as browser,
    ):
        assert run(store, "pick") == status
    browser.assert_not_called()
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_pick_empty_results_do_not_start_fzf(tmp_path, capsys):
    store = tmp_path / "store"
    bookmark(store)
    with patch("bm.commands.subprocess.run") as process:
        assert run(store, "pick", "--host", "missing.test") == 1
    process.assert_not_called()
    assert "no bookmarks match" in capsys.readouterr().err


@pytest.mark.parametrize(
    "status, selection", [(2, ""), (0, ""), (0, "0\tforged selection\n"), (0, "0\ta\n1\tb\n")]
)
def test_pick_rejects_failures_and_forged_selections(tmp_path, capsys, status, selection):
    store = tmp_path / "store"
    bookmark(store)
    result = subprocess.CompletedProcess(["fzf"], status, selection)
    with (
        patch("bm.commands.subprocess.run", return_value=result),
        patch("bm.commands.webbrowser.open") as browser,
    ):
        assert run(store, "pick") == 2
    browser.assert_not_called()
    assert capsys.readouterr().err


@pytest.mark.skipif(shutil.which("fzf") is None, reason="optional fzf not installed")
def test_real_fzf_selection_protocol_without_tty_or_browser(tmp_path, capsys):
    store = tmp_path / "store"
    bookmark(store)
    real_run = subprocess.run

    def choose(command, **kwargs):
        return real_run([*command, "--filter=Python"], **kwargs)

    with patch("bm.commands.subprocess.run", side_effect=choose):
        assert run(store, "pick", "--print", "url") == 0
    assert capsys.readouterr().out == "https://example.com\n"


def test_pick_keeps_open_scheme_safeguard(tmp_path, capsys):
    store = tmp_path / "store"
    bookmark(store, url="javascript:alert(1)")
    with (
        patch("bm.commands.subprocess.run", side_effect=fzf_choose),
        patch("bm.commands.webbrowser.open") as browser,
    ):
        assert run(store, "pick") == 1
    browser.assert_not_called()
    assert "refusing to open" in capsys.readouterr().err
