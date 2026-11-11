"""CLI contracts for copying bookmark URLs without opening or mutating them."""

from unittest.mock import patch

import pytest

from bm.cli import main
from bm.errors import BmError
from bm.utils import rid


def run(store, *args):
    return main(["--store", str(store), *args])


@pytest.mark.parametrize("token", ["dev/docs", rid("https://example.com/docs")])
def test_copy_url_without_browser_or_mutation(store, write_bm, capsys, token):
    bookmark = write_bm("dev/docs", url="https://example.com/docs", body="Private notes")
    before = bookmark.read_bytes()
    with (
        patch("bm.commands.write_clipboard") as clipboard,
        patch("bm.commands.webbrowser.open") as browser,
    ):
        assert run(store, "copy", token) == 0
    clipboard.assert_called_once_with("https://example.com/docs")
    browser.assert_not_called()
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "Copied URL to clipboard.\n"
    assert bookmark.read_bytes() == before


@pytest.mark.parametrize("kind", ["missing", "no-url", "ambiguous"])
def test_copy_invalid_entry_does_not_touch_clipboard(store, write_bm, capsys, kind):
    token = "missing"
    if kind == "no-url":
        write_bm("empty", title="No URL")
        token = "empty"
    if kind == "ambiguous":
        write_bm("a/docs", url="https://a.test")
        write_bm("b/docs", url="https://b.test")
        token = "docs"
    with patch("bm.commands.write_clipboard") as clipboard:
        assert run(store, "copy", token) == 1
    clipboard.assert_not_called()
    assert capsys.readouterr().out == ""


def test_copy_helper_failure_is_not_reported_as_success(store, write_bm, capsys):
    write_bm("docs", url="https://example.com")
    with patch(
        "bm.commands.write_clipboard", side_effect=BmError("clipboard unavailable", exit_code=2)
    ):
        assert run(store, "copy", "docs") == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "Copied" not in output.err
    assert "clipboard unavailable" in output.err
