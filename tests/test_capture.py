"""Command and workflow contracts for capture."""

import argparse
import io
from pathlib import Path
from unittest.mock import patch

import pytest

from bm.cli import main
from bm.commands import (
    cmd_add,
)
from bm.errors import BmError, UnsafePathError
from bm.io import build_text, load_entry
from bm.store import Store
from bm.utils import create_slug_from_url, rid


def run(store, *args):
    return main(["--store", str(store), *args])


class TestCmdAdd:
    """Test cmd_add function."""

    def test_add_basic(self, tmp_path):
        """Should create bookmark file."""
        store = tmp_path / "store"
        store.mkdir()
        args = argparse.Namespace()
        args.store = str(store)
        args.url = "https://example.com"
        args.id = None
        args.path = None
        args.name = "Example"
        args.tags = "tag1,tag2"
        args.description = "Notes"
        args.force = False
        args.edit = False

        with patch("bm.editor.launch_editor"):
            cmd_add(args)

        # Should create file
        files = list(store.glob("*.bm"))
        assert len(files) == 1
        fpath = files[0]
        content = fpath.read_text()
        assert "url: https://example.com" in content
        assert "title: Example" in content
        assert "tags: [tag1, tag2]" in content

    def test_add_no_force_race_does_not_overwrite_destination(self, tmp_path, monkeypatch):
        """A concurrent creator wins a no-force add without being overwritten."""
        import argparse

        from bm.store import Store

        store = tmp_path / "store"
        store.mkdir()
        args = argparse.Namespace(
            store=str(store),
            url="https://example.com",
            id="entry",
            path=None,
            name="New",
            tags=None,
            description=None,
            force=False,
            edit=False,
        )
        real_create = Store.create_entry

        def interfere(current_store, path, data):
            path.write_text("raced", encoding="utf-8")
            return real_create(current_store, path, data)

        monkeypatch.setattr(Store, "create_entry", interfere)
        with pytest.raises(BmError) as exc_info:
            cmd_add(args)
        assert "bookmark exists" in str(exc_info.value)
        assert (store / "entry.bm").read_text(encoding="utf-8") == "raced"

    def test_add_generated_slug_excludes_url_userinfo(self, tmp_path):
        """Auto-generated filenames must not leak URL credentials/userinfo."""
        store = tmp_path / "store"
        store.mkdir()
        args = argparse.Namespace()
        args.store = str(store)
        args.url = "https://user:pass@example.com/path"
        args.id = None
        args.path = None
        args.name = None
        args.tags = None
        args.description = None
        args.force = False
        args.edit = False

        cmd_add(args)

        files = list(store.glob("*.bm"))
        assert len(files) == 1
        assert files[0].name.startswith("example-com-path-")
        assert "user" not in files[0].name
        assert "pass" not in files[0].name

    def test_add_force_overwrite(self, tmp_path):
        """Should overwrite with --force."""
        store = tmp_path / "store"
        store.mkdir()
        args = argparse.Namespace()
        args.store = str(store)
        args.url = "https://example.com"
        args.id = None
        args.path = None
        args.name = "Example"
        args.tags = "tag1,tag2"
        args.description = "Notes"
        args.force = False
        args.edit = False

        with patch("bm.editor.launch_editor"):
            cmd_add(args)

        # Second add without force should fail
        with patch("bm.editor.launch_editor"):
            with pytest.raises(BmError):
                cmd_add(args)

        args.force = True
        with patch("bm.editor.launch_editor"):
            cmd_add(args)  # Should succeed

    @pytest.mark.parametrize("change", ["modified", "deleted", "appeared", "unchanged"])
    def test_force_edit_uses_the_pre_editor_destination(
        self, store, tmp_path, capsys, monkeypatch, change
    ):
        monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
        path = store / "entry.bm"
        if change != "appeared":
            path.write_bytes(b"old contents")
        edited = b"---\n# draft\nurl: https://edited.test\n---\nmy notes\n"

        def edit(draft):
            draft.write_bytes(edited)
            if change in {"modified", "appeared"}:
                path.write_bytes(b"new external contents")
            elif change == "deleted":
                path.unlink()

        with patch("bm.editor.launch_editor", side_effect=edit):
            status = run(store, "add", "https://initial.test", "--id", "entry", "--force", "--edit")
        output = capsys.readouterr()
        if change == "unchanged":
            assert status == 0 and output.out.strip() == rid("https://edited.test")
            assert load_entry(path)[1] == "my notes\n"
            assert not list(tmp_path.glob("bm-edit-*"))
        else:
            assert status == 1 and not output.out
            draft = Path(output.err.split("Edited draft kept at: ")[1].rstrip("\n"))
            assert not draft.is_relative_to(store) and draft.read_bytes() == edited
            if change == "deleted":
                assert not path.exists()
            else:
                assert path.read_bytes() == b"new external contents"

    def test_add_edit_replaces_fields_and_preserves_created(self, tmp_path):
        """Editor edits should fully replace meta (only created is preserved)."""
        import argparse

        store = tmp_path / "store"
        store.mkdir()
        args = argparse.Namespace(
            store=str(store),
            url="https://example.com",
            id=None,
            path=None,
            name="Original",
            tags="orig1,orig2",
            description="orig body",
            force=False,
            edit=True,
        )

        def fake_editor(path):
            # Simulate user keeping url+title, removing tags, setting notes.
            path.write_text("---\nurl: https://example.com\ntitle: Edited\n---\nedited body\n")

        with patch("bm.editor.launch_editor", side_effect=fake_editor):
            cmd_add(args)

        fpath = next(store.glob("*.bm"))
        content = fpath.read_text()
        assert "title: Edited" in content
        # Tags removed in editor must NOT survive merge.
        assert "tags:" not in content
        assert "edited body" in content
        # created from pre-edit meta must be preserved.
        assert "created:" in content

    def test_add_edit_warns_when_url_changed(self, tmp_path, capsys):
        """Should warn when editor changes the URL (slug/url mismatch)."""
        import argparse

        store = tmp_path / "store"
        store.mkdir()
        args = argparse.Namespace(
            store=str(store),
            url="https://original.example.com",
            id=None,
            path=None,
            name=None,
            tags=None,
            description=None,
            force=False,
            edit=True,
        )

        def change_url(path):
            path.write_text("---\nurl: https://changed.example.com\n---\n")

        with patch("bm.editor.launch_editor", side_effect=change_url):
            cmd_add(args)
        captured = capsys.readouterr()
        assert "url changed in editor" in captured.err

    def test_add_edit_dies_when_url_cleared(self, tmp_path):
        """Should refuse to write when editor leaves url blank."""
        import argparse

        store = tmp_path / "store"
        store.mkdir()
        args = argparse.Namespace(
            store=str(store),
            url="https://example.com",
            id=None,
            path=None,
            name=None,
            tags=None,
            description=None,
            force=False,
            edit=True,
        )

        def clear_url(path):
            path.write_text("---\ntitle: nothing\n---\n")

        with patch("bm.editor.launch_editor", side_effect=clear_url):
            with pytest.raises(BmError):
                cmd_add(args)

    def test_add_rejects_absolute_folder_before_normalizing(self, tmp_path):
        """An absolute --path must not be silently converted to a slug."""
        import argparse

        store = tmp_path / "store"
        store.mkdir()
        args = argparse.Namespace(
            store=str(store),
            url="https://example.com",
            id=None,
            path="/outside",
            name=None,
            tags=None,
            description=None,
            force=False,
            edit=False,
        )

        with pytest.raises(UnsafePathError):
            cmd_add(args)
        assert not list(store.glob("*.bm"))


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


@pytest.mark.parametrize("stdin", [False, True])
def test_batch_uses_one_advisory_scan_and_keeps_add_contracts(
    store, tmp_path, write_bm, monkeypatch, capsys, stdin
):
    first, second = "https://example.test/docs", "http://www.example.test/docs/#fragment"
    original = write_bm("original", url=first, body="untouched")
    before = original.read_bytes()
    data = f"  {first}  \n\n{second}\n"
    if stdin:
        monkeypatch.setattr("sys.stdin", io.StringIO(data))
        source = "-"
    else:
        source = str(tmp_path / "urls.txt")
        (tmp_path / "urls.txt").write_text(data, encoding="utf-8")

    original_scan = Store.iter_entries
    scans = []

    def counted_scan(self, **kwargs):
        scans.append(kwargs)
        return original_scan(self, **kwargs)

    monkeypatch.setattr(Store, "iter_entries", counted_scan)
    assert run(store, "add", "--batch", source, "-p", "Reading", "-t", "inbox", "-d", "Notes") == 0
    output = capsys.readouterr()
    assert output.out.splitlines() == [rid(first), rid(second)]
    assert output.err.count("equivalent URL already stored at 'original'") == 2
    first_slug = create_slug_from_url(first)
    assert f"equivalent URL already stored at 'reading/{first_slug}'" in output.err
    assert len(scans) == 1 and scans[0]["meta_only"]
    for url in (first, second):
        meta, body = load_entry(store / "reading" / (create_slug_from_url(url) + ".bm"))
        assert (meta["url"], meta["tags"], body) == (url, ["inbox"], "Notes\n")
    assert original.read_bytes() == before


def test_resume_retries_a_partial_batch_without_rewriting_or_reprinting(store, tmp_path, capsys):
    first, second = "https://first.test", "https://second.test"
    source = tmp_path / "urls.txt"
    args = ["add", "--batch", str(source), "-n", "Docs", "-t", "inbox", "-d", "Reading"]
    source.write_text(f"{first}\ninvalid line\n", encoding="utf-8")
    assert run(store, *args) == 1
    assert capsys.readouterr().out.splitlines() == [rid(first)]
    path = store / (create_slug_from_url(first) + ".bm")
    original = path.read_bytes().replace(
        b"title: Docs", b"# local comment\nx-extra: keep\ntitle: Docs"
    )
    original = original.replace(b"\n", b"\r\n")
    path.write_bytes(original)

    source.write_text(f"{first}\n{second}\n", encoding="utf-8")
    assert run(store, *args, "--resume") == 0
    output = capsys.readouterr()
    assert (
        output.out.splitlines() == [rid(second)]
        and "skipped matching batch entries: 1" in output.err
    )
    assert path.read_bytes() == original

    with patch.object(Store, "create_entry", side_effect=AssertionError("unexpected create")):
        assert run(store, *args, "--resume") == 0
    output = capsys.readouterr()
    assert not output.out and "skipped matching batch entries: 2" in output.err
    assert run(store, *args) == 1  # Default batch mode still refuses the collision.
    assert path.read_bytes() == original


@pytest.mark.parametrize("different", ["url", "title", "tags", "body"])
def test_resume_does_not_treat_a_different_capture_as_saved(store, monkeypatch, capsys, different):
    url = "https://example.test/page"
    meta = {"url": url, "title": "Docs", "tags": ["inbox"]}
    body = "Notes\n"
    if different == "url":
        meta["url"] = "http://www.example.test/page/#part"  # Same dedupe key, NOT the same URL.
    elif different == "body":
        body = "different\n"
    else:
        meta[different] = ["other"] if different == "tags" else "other"
    path = store / (create_slug_from_url(url) + ".bm")
    before = build_text(meta, body).encode()
    path.write_bytes(before)
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{url}\nhttps://later.test\n"))
    assert (
        run(store, "add", "--batch", "-", "--resume", "-n", "Docs", "-t", "inbox", "-d", "Notes")
        == 1
    )
    output = capsys.readouterr()
    assert not output.out and "stdin:1" in output.err and "not overwritten" in output.err
    assert list(store.iterdir()) == [path] and path.read_bytes() == before


def test_resume_is_batch_only(store, capsys):
    assert run(store, "add", "--resume", "https://example.test") == 2
    assert "--resume requires --batch" in capsys.readouterr().err
    assert not list(store.iterdir())


@pytest.mark.parametrize("problem", ["bad URL", "collision"])
def test_failed_batch_keeps_only_completed_adds(store, write_bm, monkeypatch, capsys, problem):
    first = "https://new.example.test"
    second = "https://existing.example.test"
    existing = write_bm(create_slug_from_url(second), url=second, body="keep")
    before = existing.read_bytes()
    bad = second if problem == "collision" else "does not belong here"
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{first}\n\n{bad}\nhttps://later.test\n"))
    assert run(store, "add", "--batch", "-") == 1
    output = capsys.readouterr()
    assert output.out.splitlines() == [rid(first)]
    assert "stdin:3" in output.err and "bookmarks saved: 1" in output.err
    assert existing.read_bytes() == before
    assert {p.stem for p in store.glob("*.bm")} == {create_slug_from_url(first), existing.stem}


@pytest.mark.parametrize(
    "extra", [["--id", "same"], ["--edit"], ["--force"], ["--clipboard"], ["https://also.test"]]
)
def test_incompatible_batch_options_do_not_read_or_write(store, capsys, extra):
    assert run(store, "add", "--batch", "-", *extra) == 2
    assert "batch" in capsys.readouterr().err
    assert not list(store.iterdir())


@pytest.mark.parametrize("text", ["", "\n \t\n", "https://[broken\n", "file:///tmp/local\n"])
def test_invalid_batch_does_not_write(store, monkeypatch, capsys, text):
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    assert run(store, "add", "--batch", "-") == 1
    assert capsys.readouterr().out == ""
    assert not list(store.iterdir())


def test_ordinary_add_does_not_read_stdin(tmp_path, capsys):
    store = tmp_path / "store"
    store.mkdir()
    # pytest's default stdin raises if read.
    assert run(store, "add", "https://example.com") == 0
    assert capsys.readouterr().out.strip()


def test_add_clipboard_url_with_metadata(store, capsys):
    url = "https://example.com/docs"
    with (
        patch("bm.clipboard.read_clipboard", return_value=f"  {url}\r\n") as clipboard,
        patch("sys.stdin.read") as stdin,
        patch("webbrowser.open") as browser,
    ):
        assert (
            run(store, "add", "--clipboard", "--id", "docs", "--name", "Docs", "--tags", "dev") == 0
        )
    clipboard.assert_called_once_with()
    stdin.assert_not_called()
    browser.assert_not_called()
    assert capsys.readouterr().out == rid(url) + "\n"
    meta, _ = load_entry(store / "docs.bm")
    assert (meta["url"], meta["title"], meta["tags"]) == (url, "Docs", ["dev"])


@pytest.mark.parametrize(
    "text", ["", " \n", "https://a.test\nhttps://b.test", "https://a.test some notes"]
)
def test_bad_clipboard_capture_does_not_write(store, capsys, text):
    with patch("bm.clipboard.read_clipboard", return_value=text):
        assert run(store, "add", "--clipboard") == 1
    assert "clipboard must contain exactly one" in capsys.readouterr().err
    assert not list(store.iterdir())


@pytest.mark.parametrize("source", ["https://example.com", "-"])
def test_clipboard_and_explicit_input_are_mutually_exclusive(store, capsys, source):
    with patch("bm.clipboard.read_clipboard") as clipboard:
        assert run(store, "add", source, "--clipboard") == 2
    clipboard.assert_not_called()
    assert "not allowed" in capsys.readouterr().err
    assert not list(store.iterdir())


@pytest.mark.parametrize(
    "text",
    [
        "private-token",
        "javascript:alert(1)",
        "https://",
        "https://[broken",
        "https://example.com:bad",
    ],
)
def test_clipboard_requires_a_structurally_valid_supported_url(store, capsys, text):
    with patch("bm.clipboard.read_clipboard", return_value=text):
        assert run(store, "add", "--clipboard") == 1
    assert "clipboard must contain a valid" in capsys.readouterr().err
    assert not list(store.iterdir())


def test_normal_add_never_reads_clipboard(store):
    with patch("bm.clipboard.read_clipboard") as clipboard:
        assert run(store, "add", "https://example.com", "--id", "normal") == 0
    clipboard.assert_not_called()


def test_missing_store_does_not_read_clipboard(tmp_path):
    with patch("bm.clipboard.read_clipboard") as clipboard:
        assert run(tmp_path / "missing", "add", "--clipboard") == 1
    clipboard.assert_not_called()


def test_clipboard_error_does_not_write(store, capsys):
    with patch(
        "bm.clipboard.read_clipboard", side_effect=BmError("clipboard unavailable", exit_code=2)
    ):
        assert run(store, "add", "--clipboard") == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "clipboard unavailable" in output.err
    assert not list(store.iterdir())


def test_clipboard_duplicate_still_warns_without_replacing(store, write_bm, capsys):
    old = write_bm("original", url="https://example.com/docs", body="Keep")
    before = old.read_bytes()
    with patch("bm.clipboard.read_clipboard", return_value="http://www.example.com/docs/#part"):
        assert run(store, "add", "--clipboard", "--id", "new") == 0
    assert "equivalent URL already stored at 'original'" in capsys.readouterr().err
    assert old.read_bytes() == before
