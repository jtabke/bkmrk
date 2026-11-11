"""Lossless backup CLI contracts and adversarial restore preflight."""

import io
import json
from pathlib import Path

import pytest

from bm.backup import BACKUP_FORMAT, build_backup
from bm.cli import main
from bm.store import Store

CONTENT = (
    "---\r\n# Keep comments and ordering\r\nurl: https://example.com\r\n"
    "title: Café\r\ntags: [one, two]\r\ncreated: 2020-01-01\r\n"
    "modified: 2021-02-03\r\ncustom: |\r\n  unknown value\r\n---\r\n"
    "\r\nNotes with trailing spaces  \r\n\r\n"
)


def run(store: Path, *args: str) -> int:
    return main(["--store", str(store), *args])


def archive(tmp_path, entries=None, **overrides):
    document = {
        "format": BACKUP_FORMAT,
        "version": 1,
        "entries": entries if entries is not None else [{"path": "a.bm", "content": CONTENT}],
    }
    document.update(overrides)
    source = tmp_path / "backup.json"
    source.write_text(json.dumps(document), encoding="utf-8")
    return source


def test_exact_round_trip_and_repeat_import(tmp_path, capsys):
    source = tmp_path / "source"
    path = source / "Folder" / "Custom Name.bm"
    path.parent.mkdir(parents=True)
    path.write_bytes(CONTENT.encode())
    output = tmp_path / "backup.json"
    assert run(source, "export", "-o", str(output)) == 0
    assert capsys.readouterr().out == ""
    document = json.loads(output.read_bytes())
    assert document["entries"] == [{"path": "Folder/Custom Name.bm", "content": CONTENT}]
    destination = tmp_path / "restored"
    assert run(destination, "import", str(output), "--dry-run") == 0
    assert not destination.exists()
    assert "Would add 1" in capsys.readouterr().out
    assert run(destination, "import", str(output)) == 0
    assert "Added 1" in capsys.readouterr().out
    assert (destination / "Folder" / "Custom Name.bm").read_bytes() == path.read_bytes()
    assert run(destination, "import", str(output)) == 0
    assert "left unchanged 1" in capsys.readouterr().out
    assert build_backup(Store(destination)) == build_backup(Store(source))


def test_default_stdout_and_empty_store(tmp_path, capsys):
    store = tmp_path / "store"
    store.mkdir()
    assert run(store, "export") == 0
    assert json.loads(capsys.readouterr().out) == {
        "format": BACKUP_FORMAT,
        "version": 1,
        "entries": [],
    }


def test_export_missing_store_does_not_create_output(tmp_path):
    output = tmp_path / "out.json"
    assert run(tmp_path / "missing", "export", "-o", str(output)) == 1
    assert not output.exists()


def test_output_no_clobber(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    output = tmp_path / "out.json"
    output.write_bytes(b"important")
    assert run(store, "export", "-o", str(output)) == 1
    assert output.read_bytes() == b"important"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out.json", "store"]


def test_conflicts_block_all_writes_and_force_keeps_unrelated(tmp_path, capsys):
    store = tmp_path / "store"
    store.mkdir()
    (store / "z.bm").write_bytes(b"old")
    (store / "unrelated.bm").write_bytes(b"keep")
    source = archive(
        tmp_path, [{"path": "a.bm", "content": CONTENT}, {"path": "z.bm", "content": CONTENT}]
    )
    assert run(store, "import", str(source)) == 1
    assert not (store / "a.bm").exists()
    assert (store / "z.bm").read_bytes() == b"old"
    assert "z.bm" in capsys.readouterr().err
    assert run(store, "import", str(source), "--force", "--dry-run") == 0
    assert "Would add 1, replace 1" in capsys.readouterr().out
    assert (store / "z.bm").read_bytes() == b"old"
    assert run(store, "import", str(source), "--force") == 0
    assert (store / "z.bm").read_bytes() == CONTENT.encode()
    assert (store / "unrelated.bm").read_bytes() == b"keep"


@pytest.mark.parametrize(
    "name",
    [
        "../escape.bm",
        "/absolute.bm",
        "C:/escape.bm",
        "a\\b.bm",
        "a//b.bm",
        "./a.bm",
        "a/../b.bm",
        ".git/config.bm",
        "a/.GIT/config.bm",
        "a.txt",
        "",
        "a\x00.bm",
    ],
)
def test_unsafe_path_preflight(tmp_path, name):
    store = tmp_path / "store"
    source = archive(
        tmp_path, [{"path": "0-valid.bm", "content": CONTENT}, {"path": name, "content": CONTENT}]
    )
    assert run(store, "import", str(source), "--force") == 1
    assert not store.exists()


@pytest.mark.parametrize(
    "overrides",
    [
        {"version": 2},
        {"version": True},
        {"entries": {}},
        {"entries": [{"path": "a.bm"}]},
        {"entries": [{"path": 3, "content": CONTENT}]},
        {"entries": [{"path": "a.bm", "content": "url: javascript:alert(1)"}]},
        {"entries": [{"path": "a.bm", "content": CONTENT.replace("https:", "javascript:")}]},
        {"entries": [{"path": "a.bm", "content": "no URL"}]},
        {"entries": [{"path": "a.bm", "content": "\ud800"}]},
        {"extra": True},
    ],
)
def test_invalid_document_does_not_create_store(tmp_path, overrides):
    source = archive(tmp_path, **overrides)
    store = tmp_path / "store"
    assert run(store, "import", str(source)) == 1
    assert not store.exists()


@pytest.mark.parametrize("names", [("a.bm", "a.bm"), ("a.bm", "a.bm/nested.bm")])
def test_duplicate_and_overlapping_destinations(tmp_path, names):
    source = archive(tmp_path, [{"path": name, "content": CONTENT} for name in names])
    store = tmp_path / "store"
    assert run(store, "import", str(source)) == 1
    assert not store.exists()


@pytest.mark.parametrize(
    "text",
    [
        '[{"path": "a", "url": "https://example.com"}]',
        "{bad",
        '{"format":"bm-backup","version":1,"version":1,"entries":[]}',
    ],
)
def test_bad_json_is_not_silently_treated_as_html(tmp_path, text):
    source = tmp_path / "bad.json"
    source.write_text(text)
    store = tmp_path / "store"
    assert run(store, "import", str(source)) == 1
    assert not store.exists()


@pytest.mark.parametrize("directory", [False, True])
def test_symlink_paths_rejected(tmp_path, directory):
    store = tmp_path / "store"
    store.mkdir()
    outside = tmp_path / "outside"
    if directory:
        outside.mkdir()
        name = "linked/a.bm"
        link = store / "linked"
    else:
        outside.write_bytes(b"keep")
        name = "linked.bm"
        link = store / name
    try:
        link.symlink_to(outside, target_is_directory=directory)
    except OSError:
        pytest.skip("symlinks unavailable")
    source = archive(tmp_path, [{"path": name, "content": CONTENT}])
    assert run(store, "import", str(source), "--force") == 1
    output = tmp_path / "out.json"
    assert run(store, "export", "-o", str(output)) == 1
    assert not output.exists()


def test_force_detects_changed_destination(tmp_path, monkeypatch, capsys):
    source = archive(tmp_path)
    store = tmp_path / "store"
    store.mkdir()
    target = store / "a.bm"
    target.write_bytes(b"old")
    original = Store.write

    def concurrent_write(self, path, data, expected=None):
        path.write_bytes(b"concurrent")
        return original(self, path, data, expected=expected)

    monkeypatch.setattr(Store, "write", concurrent_write)
    assert run(store, "import", str(source), "--force") == 1
    assert target.read_bytes() == b"concurrent"
    assert "earlier files may have been written" in capsys.readouterr().err


def test_create_race_does_not_clobber(tmp_path, monkeypatch):
    source = archive(tmp_path)
    store = tmp_path / "store"
    original = Store.create_entry

    def concurrent_create(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"concurrent")
        return original(self, path, data)

    monkeypatch.setattr(Store, "create_entry", concurrent_create)
    assert run(store, "import", str(source)) == 1
    assert (store / "a.bm").read_bytes() == b"concurrent"


def test_netscape_dry_run_does_not_create_store(tmp_path, capsys):
    source = tmp_path / "bookmarks.html"
    source.write_text('<DL><p><DT><A HREF="https://example.com">Example</A></DL>')
    store = tmp_path / "store"
    assert run(store, "import", str(source), "--dry-run") == 0
    assert not store.exists()
    assert "Would add 1, replace 0, skip existing 0; invalid 0." in capsys.readouterr().out


def test_export_is_sorted_and_excludes_non_bookmark_files(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    for name in ("z.bm", "a.bm"):
        (store / name).write_bytes(CONTENT.encode())
    (store / "README.txt").write_text("not a bookmark")
    (store / ".git").mkdir()
    (store / ".git" / "config").write_bytes(b"not a bookmark")
    document = json.loads(build_backup(Store(store)))
    assert [entry["path"] for entry in document["entries"]] == ["a.bm", "z.bm"]


def test_export_invalid_utf8_leaves_no_output(tmp_path, capsys):
    store = tmp_path / "store"
    store.mkdir()
    (store / "a.bm").write_bytes(CONTENT.encode())
    (store / "z.bm").write_bytes(b"\xff")
    output = tmp_path / "out.json"
    assert run(store, "export", "-o", str(output)) == 1
    assert not output.exists()
    assert "not valid UTF-8" in capsys.readouterr().err


def test_preview_lists_conflicts_in_path_order_without_changes(tmp_path, capsys):
    store = tmp_path / "store"
    store.mkdir()
    for name in ("a.bm", "z.bm"):
        (store / name).write_bytes(b"old")
    source = archive(
        tmp_path,
        [
            {"path": "z.bm", "content": CONTENT},
            {"path": "a.bm", "content": CONTENT},
        ],
    )
    before = {path.name: path.read_bytes() for path in store.iterdir()}
    assert run(store, "import", str(source), "--dry-run") == 1
    captured = capsys.readouterr()
    assert "conflicts 2" in captured.out
    assert captured.err.index("  a.bm") < captured.err.index("  z.bm")
    assert {path.name: path.read_bytes() for path in store.iterdir()} == before


def test_blocked_parent_and_directory_destination_preflight(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    (store / "blocked").write_bytes(b"keep")
    (store / "directory.bm").mkdir()
    for name in ("blocked/a.bm", "directory.bm"):
        source = archive(
            tmp_path,
            [
                {"path": "a.bm", "content": CONTENT},
                {"path": name, "content": CONTENT},
            ],
        )
        assert run(store, "import", str(source), "--force") == 1
        assert not (store / "a.bm").exists()
    assert (store / "blocked").read_bytes() == b"keep"


def test_json_extension_with_non_object_input_is_rejected(tmp_path):
    source = tmp_path / "bad.json"
    source.write_text("null")
    store = tmp_path / "store"
    assert run(store, "import", str(source)) == 1
    assert not store.exists()


def test_stdin_backup_preview_and_restore_preserve_bytes(
    tmp_path, monkeypatch, capsys, bookmark_factory
):
    original = tmp_path / "original"
    target = bookmark_factory(original)
    # Normalize native newlines first: write_text already emits CRLF on Windows.
    content = target.read_bytes().replace(b"\r\n", b"\n")
    target.write_bytes(content.replace(b"\n", b"\r\n") + b"\r\nNotes  \r\n")
    assert b"\r\r\n" not in target.read_bytes()
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


def test_finalized_restore_plan_is_immutable(tmp_path):
    from dataclasses import FrozenInstanceError

    from bm.backup import apply_restore, plan_restore
    from bm.urls import ALLOWED_URL_SCHEMES

    store = Store(tmp_path / "store")
    entries = [{"path": "entry.bm", "content": CONTENT}]
    plan = plan_restore(store, entries, ALLOWED_URL_SCHEMES, force=False)
    assert isinstance(plan.items, tuple)
    assert isinstance(plan.conflicts, tuple)
    with pytest.raises(FrozenInstanceError):
        plan.unchanged = 99
    with pytest.raises(FrozenInstanceError):
        plan.items[0].content = b"replacement"
    entries[0]["content"] = "changed after preflight"
    apply_restore(store, plan)
    assert (store.root / "entry.bm").read_bytes() == CONTENT.encode("utf-8")
