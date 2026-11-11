"""Direct opening and explicit terminal selection share one public command."""

import json
import shutil
import subprocess
from unittest.mock import patch

import pytest

from bm.cli import main
from bm.utils import rid


def run(store, *args):
    return main(["--store", str(store), *args])


def choose_first(command, **kwargs):
    assert command[0] == "fzf"
    assert "--select-1" not in command
    assert "--exit-0" not in command
    return subprocess.CompletedProcess(command, 0, kwargs["input"].splitlines()[0] + "\n")


@pytest.mark.parametrize("token", ["dev/docs", rid("https://example.com/docs")])
def test_direct_open_needs_neither_terminal_nor_fzf(store, write_bm, token, capsys):
    bookmark = write_bm("dev/docs", url="https://example.com/docs")
    before = bookmark.read_bytes()
    with (
        patch("sys.stdin.isatty", return_value=False),
        patch("sys.stderr.isatty", return_value=False),
        patch("bm.commands.subprocess.run") as process,
        patch("bm.commands.webbrowser.open", return_value=True) as browser,
    ):
        assert run(store, "open", token) == 0
    process.assert_not_called()
    browser.assert_called_once_with("https://example.com/docs")
    assert capsys.readouterr().out == "https://example.com/docs\n"
    assert bookmark.read_bytes() == before


@pytest.mark.parametrize("action", [["--print", "url"], ["--print", "id"], ["--copy"]])
def test_direct_print_or_copy_needs_no_terminal_or_fzf(store, write_bm, capsys, action):
    write_bm("docs", url="https://example.com")
    with (
        patch("sys.stdin.isatty", return_value=False),
        patch("sys.stderr.isatty", return_value=False),
        patch("bm.commands.subprocess.run") as process,
        patch("bm.commands.webbrowser.open") as browser,
        patch("bm.commands.write_clipboard") as clipboard,
    ):
        assert run(store, "open", "docs", *action) == 0
    browser.assert_not_called()
    process.assert_not_called()
    output = capsys.readouterr()
    if action == ["--copy"]:
        clipboard.assert_called_once_with("https://example.com")
        assert output.out == ""
        assert output.err == "Copied URL to clipboard.\n"
    else:
        clipboard.assert_not_called()
        expected = rid("https://example.com") if action[-1] == "id" else "https://example.com"
        assert output.out == expected + "\n"
        assert output.err == ""


@pytest.mark.parametrize("stdin_tty,stderr_tty", [(False, False), (False, True), (True, False)])
@pytest.mark.parametrize("arguments", [[], ["--search", "python"], ["--tag", "dev", "--copy"]])
def test_selection_requires_input_and_ui_terminals(
    store, write_bm, capsys, stdin_tty, stderr_tty, arguments
):
    write_bm("dev/docs", url="https://example.com", title="Python")
    with (
        patch("sys.stdin.isatty", return_value=stdin_tty),
        patch("sys.stderr.isatty", return_value=stderr_tty),
        patch("bm.commands.subprocess.run") as process,
        patch("bm.commands.webbrowser.open") as browser,
        patch("bm.commands.write_clipboard") as clipboard,
    ):
        assert run(store, "open", *arguments) == 2
    process.assert_not_called()
    browser.assert_not_called()
    clipboard.assert_not_called()
    output = capsys.readouterr()
    assert output.out == ""
    assert "requires a terminal on stdin and stderr" in output.err
    assert "ID/path" in output.err
    assert "bm search" in output.err


def test_selection_allows_redirected_stdout_and_still_confirms_single_result(
    store, write_bm, capsys, interactive_terminal
):
    write_bm("docs", url="https://example.com", title="Python")
    with (
        patch("sys.stdout.isatty", return_value=False),
        patch("bm.commands.subprocess.run", side_effect=choose_first) as process,
        patch("bm.commands.webbrowser.open") as browser,
    ):
        assert run(store, "open", "--search", "python", "--print", "url") == 0
    process.assert_called_once()
    browser.assert_not_called()
    output = capsys.readouterr()
    assert output.out == "https://example.com\n"
    assert output.err == ""


def test_unresolved_token_never_falls_back_to_text_search(store, write_bm, capsys):
    write_bm("unrelated", url="https://python.example", title="Python", body="Python")
    with (
        patch("bm.commands.subprocess.run") as process,
        patch("bm.commands.webbrowser.open") as browser,
    ):
        assert run(store, "open", "python") == 1
    process.assert_not_called()
    browser.assert_not_called()
    assert "bookmark not found" in capsys.readouterr().err


@pytest.mark.parametrize(
    "arguments",
    [
        ["docs", "--search", "python"],
        ["--search", "python", "docs"],
        ["docs", "--tag", "dev"],
        ["docs", "--host", "example.com"],
        ["docs", "--path", "dev"],
        ["docs", "--since", "2025-01-01"],
        ["docs", "--tag", ""],
        ["docs", "--copy", "--print", "url"],
        ["--search", "python", "--copy", "--print", "url"],
        ["--allow-scheme"],
        ["--search", "python", "--allow-scheme"],
        ["docs", "--copy", "--allow-scheme"],
        ["docs", "--print", "url", "--allow-scheme"],
        ["--search", "python", "--unknown"],
        ["--search"],
        ["--search", "python", "async"],
    ],
)
def test_ambiguous_or_irrelevant_options_fail_without_side_effects(store, capsys, arguments):
    with (
        patch("bm.commands.subprocess.run") as process,
        patch("bm.commands.webbrowser.open") as browser,
        patch("bm.commands.write_clipboard") as clipboard,
    ):
        assert run(store, "open", *arguments) == 2
    process.assert_not_called()
    browser.assert_not_called()
    clipboard.assert_not_called()
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err


def test_direct_allow_scheme_remains_explicit(store, write_bm):
    write_bm("custom", url="custom:example")
    with patch("bm.commands.webbrowser.open", return_value=True) as browser:
        assert run(store, "open", "custom", "--allow-scheme") == 0
    browser.assert_called_once_with("custom:example")


def test_selection_copy_failure_never_opens_browser(store, write_bm, interactive_terminal, capsys):
    from bm.errors import BmError

    write_bm("docs", url="https://example.com")
    with (
        patch("bm.commands.subprocess.run", side_effect=choose_first),
        patch("bm.commands.write_clipboard", side_effect=BmError("clipboard failed", exit_code=2)),
        patch("bm.commands.webbrowser.open") as browser,
    ):
        assert run(store, "open", "--copy") == 2
    browser.assert_not_called()
    output = capsys.readouterr()
    assert output.out == ""
    assert "Copied URL" not in output.err


def test_search_remains_noninteractive_and_machine_readable(store, write_bm, capsys):
    write_bm("docs", url="https://example.com", title="Python")
    with (
        patch("bm.commands.subprocess.run") as process,
        patch("bm.commands.webbrowser.open") as browser,
        patch("bm.commands.write_clipboard") as clipboard,
    ):
        assert run(store, "search", "python", "--json") == 0
    process.assert_not_called()
    browser.assert_not_called()
    clipboard.assert_not_called()
    assert json.loads(capsys.readouterr().out)[0]["path"] == "docs"


def test_unreleased_pick_command_is_removed(store, capsys):
    assert run(store, "pick") == 2
    assert "invalid choice" in capsys.readouterr().err
    assert run(store, "--help") == 0
    assert "pick" not in capsys.readouterr().out


@pytest.mark.parametrize("field", ["id", "url"])
def test_open_selection_prints_without_opening_browser(
    tmp_path, capsys, field, interactive_terminal, bookmark_factory
):
    store = tmp_path / "store"
    bookmark_factory(store)
    with (
        patch("bm.commands.subprocess.run", side_effect=choose_first),
        patch("bm.commands.webbrowser.open") as browser,
    ):
        assert run(store, "open", "--print", field) == 0
    browser.assert_not_called()
    expected = rid("https://example.com") if field == "id" else "https://example.com"
    assert capsys.readouterr().out == expected + "\n"


def test_open_selection_opens_selected_url_and_does_not_mutate_store(
    tmp_path, capsys, interactive_terminal, bookmark_factory
):
    store = tmp_path / "store"
    path = bookmark_factory(store)
    before = path.read_bytes()
    with (
        patch("bm.commands.subprocess.run", side_effect=choose_first),
        patch("bm.commands.webbrowser.open", return_value=True) as browser,
    ):
        assert run(store, "open") == 0
    browser.assert_called_once_with("https://example.com")
    assert capsys.readouterr().out == "https://example.com\n"
    assert path.read_bytes() == before


def test_open_selection_filters_and_sanitizes_untrusted_display(
    tmp_path, monkeypatch, capsys, interactive_terminal, bookmark_factory
):
    store = tmp_path / "store"
    bookmark_factory(store, "dev/guide.bm", title="Python\x1b[31m")
    bookmark_factory(store, "other/skip.bm", url="https://other.test")
    monkeypatch.setenv("FZF_DEFAULT_OPTS", "--bind=start:execute(touch should-not-run)")
    monkeypatch.setenv("FZF_DEFAULT_OPTS_FILE", "/should-not-read")

    def choose(command, **kwargs):
        assert "\x1b" not in kwargs["input"]
        assert "skip" not in kwargs["input"]
        assert "FZF_DEFAULT_OPTS" not in kwargs["env"]
        assert "FZF_DEFAULT_OPTS_FILE" not in kwargs["env"]
        assert "shell" not in kwargs
        return choose_first(command, **kwargs)

    with patch("bm.commands.subprocess.run", side_effect=choose):
        assert (
            run(
                store,
                "open",
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


def test_open_selection_missing_fzf_is_actionable(
    tmp_path, capsys, interactive_terminal, bookmark_factory
):
    store = tmp_path / "store"
    bookmark_factory(store)
    with patch("bm.commands.subprocess.run", side_effect=FileNotFoundError("fzf")):
        assert run(store, "open", "--print", "url") == 2
    assert "install fzf" in capsys.readouterr().err


@pytest.mark.parametrize("status", [1, 130])
def test_open_selection_cancellation_is_quiet_and_opens_nothing(
    tmp_path, capsys, status, interactive_terminal, bookmark_factory
):
    store = tmp_path / "store"
    bookmark_factory(store)
    result = subprocess.CompletedProcess(["fzf"], status, "")
    with (
        patch("bm.commands.subprocess.run", return_value=result),
        patch("bm.commands.webbrowser.open") as browser,
    ):
        assert run(store, "open") == status
    browser.assert_not_called()
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_open_selection_empty_results_do_not_start_fzf(
    tmp_path, capsys, interactive_terminal, bookmark_factory
):
    store = tmp_path / "store"
    bookmark_factory(store)
    with patch("bm.commands.subprocess.run") as process:
        assert run(store, "open", "--host", "missing.test") == 1
    process.assert_not_called()
    assert "no bookmarks match" in capsys.readouterr().err


@pytest.mark.parametrize(
    "status, selection", [(2, ""), (0, ""), (0, "0\tforged selection\n"), (0, "0\ta\n1\tb\n")]
)
def test_open_selection_rejects_failures_and_forged_selections(
    tmp_path, capsys, status, selection, interactive_terminal, bookmark_factory
):
    store = tmp_path / "store"
    bookmark_factory(store)
    result = subprocess.CompletedProcess(["fzf"], status, selection)
    with (
        patch("bm.commands.subprocess.run", return_value=result),
        patch("bm.commands.webbrowser.open") as browser,
    ):
        assert run(store, "open") == 2
    browser.assert_not_called()
    assert capsys.readouterr().err


@pytest.mark.skipif(shutil.which("fzf") is None, reason="optional fzf not installed")
def test_real_fzf_selection_protocol_with_simulated_terminal(
    tmp_path, capsys, interactive_terminal, bookmark_factory
):
    store = tmp_path / "store"
    bookmark_factory(store)
    real_run = subprocess.run

    def choose(command, **kwargs):
        return real_run([*command, "--filter=Python"], **kwargs)

    with patch("bm.commands.subprocess.run", side_effect=choose):
        assert run(store, "open", "--print", "url") == 0
    assert capsys.readouterr().out == "https://example.com\n"


def test_open_selection_keeps_open_scheme_safeguard(
    tmp_path, capsys, interactive_terminal, bookmark_factory
):
    store = tmp_path / "store"
    bookmark_factory(store, url="javascript:alert(1)")
    with (
        patch("bm.commands.subprocess.run", side_effect=choose_first),
        patch("bm.commands.webbrowser.open") as browser,
    ):
        assert run(store, "open") == 1
    browser.assert_not_called()
    diagnostic = capsys.readouterr().err
    assert "refusing to open" in diagnostic
    assert "bm open <ID|path> --allow-scheme" in diagnostic


@pytest.mark.parametrize(
    "arguments",
    [
        ["--search", "python async", "--tag", "dev", "--print", "url"],
        ["--tag", "dev", "--search", "python async", "--print", "url"],
        ["--print", "url", "--search", "python async", "--tag", "dev"],
    ],
)
def test_open_selection_prefilters_with_search_terms_and_notes(
    store, write_bm, capsys, arguments, interactive_terminal
):
    write_bm("dev/docs", url="https://example.com/docs", title="Python", body="Async notes")
    write_bm("dev/skip", url="https://skip.test", title="Python", body="Unrelated")
    write_bm("other/skip", url="https://other.test", title="Python async")

    def choose(command, **kwargs):
        assert "dev/docs" in kwargs["input"]
        assert "skip" not in kwargs["input"]
        return choose_first(command, **kwargs)

    with (
        patch("bm.commands.subprocess.run", side_effect=choose),
        patch("bm.commands.webbrowser.open") as browser,
    ):
        assert run(store, "open", *arguments) == 0
    browser.assert_not_called()
    assert capsys.readouterr().out == "https://example.com/docs\n"


def test_open_selection_search_then_opens_selected_website(store, write_bm, interactive_terminal):
    write_bm("docs", url="https://example.com/docs", title="Python")
    with (
        patch("bm.commands.subprocess.run", side_effect=choose_first),
        patch("bm.commands.webbrowser.open", return_value=True) as browser,
    ):
        assert run(store, "open", "--search", "python") == 0
    browser.assert_called_once_with("https://example.com/docs")


def test_open_selection_can_copy_instead_of_opening(store, write_bm, capsys, interactive_terminal):
    write_bm("docs", url="https://example.com/docs", title="Python")
    with (
        patch("bm.commands.subprocess.run", side_effect=choose_first),
        patch("bm.commands.write_clipboard") as clipboard,
        patch("bm.commands.webbrowser.open") as browser,
    ):
        assert run(store, "open", "--search", "python", "--copy") == 0
    clipboard.assert_called_once_with("https://example.com/docs")
    browser.assert_not_called()
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "Copied URL to clipboard.\n"


def test_open_selection_copy_and_print_are_mutually_exclusive(store, interactive_terminal):
    with (
        patch("bm.commands.subprocess.run") as process,
        patch("bm.commands.write_clipboard") as clipboard,
    ):
        assert run(store, "open", "--copy", "--print", "url") == 2
    process.assert_not_called()
    clipboard.assert_not_called()


def test_open_selection_cancellation_never_copies(store, write_bm, capsys, interactive_terminal):
    write_bm("docs", url="https://example.com/docs")
    with (
        patch("bm.commands.subprocess.run", return_value=subprocess.CompletedProcess([], 130, "")),
        patch("bm.commands.write_clipboard") as clipboard,
    ):
        assert run(store, "open", "--copy") == 130
    clipboard.assert_not_called()
    output = capsys.readouterr()
    assert output.out == output.err == ""


def test_open_selection_unknown_option_is_still_a_parser_error(store, interactive_terminal):
    with patch("bm.commands.subprocess.run") as process:
        assert run(store, "open", "--search", "python", "--unknown") == 2
    process.assert_not_called()


def test_open_selection_no_search_matches_does_not_start_fzf(
    store, write_bm, capsys, interactive_terminal
):
    write_bm("docs", url="https://example.com")
    with patch("bm.commands.subprocess.run") as process:
        assert run(store, "open", "--search", "nonexistent") == 1
    process.assert_not_called()
    assert "no bookmarks match" in capsys.readouterr().err
