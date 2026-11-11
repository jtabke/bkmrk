"""Advisory duplicate detection must preserve add's output and mutation contracts."""

from unittest.mock import patch

import pytest

from bm.cli import main
from bm.io import load_entry
from bm.utils import rid


def run(store, *args):
    return main(["--store", str(store), "add", *args])


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/docs",
        "http://www.EXAMPLE.com:80/docs/#section",
        "https://example.com/a/../docs/",
    ],
)
def test_equivalent_url_warns_without_replacing(store, write_bm, capsys, url):
    existing = write_bm("dev/original", url="https://example.com/docs", body="Keep notes")
    before = existing.read_bytes()
    assert run(store, url, "--id", "new", "--description", "New notes") == 0
    output = capsys.readouterr()
    assert output.out == rid(url) + "\n"
    assert "equivalent URL already stored at 'dev/original'" in output.err
    assert "bm show <path>" in output.err
    assert "bm open <path>" in output.err
    assert "Saved separately" in output.err
    assert existing.read_bytes() == before
    assert load_entry(store / "new.bm")[1] == "New notes\n"


def test_distinct_queries_do_not_warn(store, write_bm, capsys):
    write_bm("old", url="https://example.com/?page=1")
    assert run(store, "https://example.com/?page=2", "--id", "new") == 0
    assert capsys.readouterr().err == ""


def test_multiple_matches_are_sorted(store, write_bm, capsys):
    for name in ("z", "a"):
        write_bm(name, url="https://example.com")
    assert run(store, "https://example.com", "--id", "new") == 0
    warnings = capsys.readouterr().err.splitlines()
    assert len(warnings) == 2
    assert "at 'a'" in warnings[0]
    assert "at 'z'" in warnings[1]


def test_force_excludes_own_destination_but_reports_other_matches(store, write_bm, capsys):
    write_bm("same", url="https://example.com")
    other = write_bm("other", url="https://example.com", body="Keep")
    before = other.read_bytes()
    assert run(store, "https://example.com", "--id", "same", "--force") == 0
    output = capsys.readouterr()
    assert "at 'other'" in output.err
    assert "at 'same'" not in output.err
    assert other.read_bytes() == before


def test_collision_still_fails_without_duplicate_warning(store, write_bm, capsys):
    existing = write_bm("same", url="https://example.com")
    before = existing.read_bytes()
    assert run(store, "https://example.com", "--id", "same") == 1
    output = capsys.readouterr()
    assert "bookmark exists" in output.err
    assert "Saved separately" not in output.err
    assert output.out == ""
    assert existing.read_bytes() == before


def test_duplicate_detection_uses_edited_url(store, write_bm, capsys):
    write_bm("existing", url="https://edited.example")

    def edit(path):
        path.write_text("---\nurl: https://edited.example\n---\n", encoding="utf-8")

    with patch("bm.commands.launch_editor", side_effect=edit):
        assert run(store, "https://original.example", "--id", "new", "--edit") == 0
    output = capsys.readouterr()
    assert output.out == rid("https://edited.example") + "\n"
    assert "at 'existing'" in output.err


def test_damaged_url_does_not_block_add(store, write_bm, capsys):
    write_bm("damaged", url="https://[broken")
    assert run(store, "https://example.com", "--id", "new") == 0
    assert capsys.readouterr().err == ""


def test_force_of_only_match_is_quiet(store, write_bm, capsys):
    write_bm("same", url="https://example.com")
    assert run(store, "https://example.com", "--id", "same", "--force") == 0
    assert capsys.readouterr().err == ""


def test_escaping_symlink_is_skipped(store, tmp_path, capsys):
    outside = tmp_path / "outside.bm"
    outside.write_text("---\nurl: https://example.com\n---\n", encoding="utf-8")
    before = outside.read_bytes()
    try:
        (store / "escape.bm").symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    assert run(store, "https://example.com", "--id", "new") == 0
    output = capsys.readouterr()
    assert "skipping escape" in output.err
    assert "equivalent URL" not in output.err
    assert outside.read_bytes() == before


def test_stdin_url_is_checked(store, write_bm, capsys):
    import io

    write_bm("existing", url="https://example.com")
    with patch("sys.stdin", io.StringIO("https://example.com\n")):
        assert run(store, "-", "--id", "new") == 0
    assert "at 'existing'" in capsys.readouterr().err
