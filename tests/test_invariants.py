"""Cross-feature storage, backup, and rewrite invariants."""

import json

import pytest

from bm.backup import build_backup, read_backup
from bm.cli import main
from bm.doctor import inspect_store
from bm.errors import UnsafePathError
from bm.io import build_text, load_entry
from bm.store import Store
from bm.urls import ALLOWED_URL_SCHEMES


@pytest.mark.parametrize("slug", [".git/hidden", "dev/.GIT/hidden", "dev/.git/hooks/entry"])
def test_reserved_namespaces_rejected_across_write_paths(tmp_path, capsys, slug):
    store = Store(tmp_path / "store")
    store.create()
    original = store.root / "original.bm"
    original.write_text(build_text({"url": "https://example.com"}, "notes"), encoding="utf-8")

    def run(*args):
        return main(["--store", str(store.root), *args])

    assert run("add", "https://example.com/new", "--id", slug) == 1
    assert run("mv", "original", slug) == 1
    raw_path = store.root / (slug + ".bm")
    with pytest.raises(UnsafePathError, match="reserved"):
        store.create_entry(raw_path, "new")
    with pytest.raises(UnsafePathError, match="reserved"):
        store.write(raw_path, "new")
    assert original.exists()
    assert not raw_path.exists()
    assert "reserved" in capsys.readouterr().err

    backup = tmp_path / "backup.json"
    backup.write_text(
        json.dumps(
            {
                "format": "bm-backup",
                "version": 1,
                "entries": [
                    {
                        "path": slug + ".bm",
                        "content": build_text({"url": "https://example.com"}, ""),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert run("import", str(backup)) == 1
    assert not raw_path.exists()


@pytest.mark.parametrize("name", [".git/hidden.bm", "dev/.GIT/hidden.bm"])
def test_legacy_reserved_bookmark_is_diagnosed_and_never_silently_omitted(tmp_path, capsys, name):
    store = Store(tmp_path / "store")
    path = store.root / name
    path.parent.mkdir(parents=True)
    content = build_text({"url": "https://example.com"}, "legacy notes")
    path.write_text(content, encoding="utf-8")
    output = tmp_path / "backup.json"

    assert main(["--store", str(store.root), "export", "-o", str(output)]) == 1
    assert "reserved" in capsys.readouterr().err
    assert not output.exists()
    assert not list(tmp_path.glob("*.tmp"))
    findings = inspect_store(store, ALLOWED_URL_SCHEMES).findings
    assert any(item.code == "reserved_bookmark_path" and item.paths == [name] for item in findings)
    assert path.read_text(encoding="utf-8") == content


@pytest.mark.parametrize("slug", ["inbox/page", "reading/éclair", "nested/.hidden/page", ".git"])
def test_accepted_destination_is_in_exact_content_backup(tmp_path, slug):
    store = Store(tmp_path / "store")
    store.create()
    path = store.path_for(slug)
    content = b"---\r\nurl: https://example.com\r\nx-extra: kept\r\n---\r\nnotes\r\n"
    store.create_entry(path, content)
    records = read_backup(build_backup(store))
    assert records == [
        {"path": path.relative_to(store.root).as_posix(), "content": content.decode()}
    ]


def test_inward_symlink_cannot_bypass_reserved_namespace(tmp_path):
    store = Store(tmp_path / "store")
    store.create()
    metadata = store.root / ".git"
    metadata.mkdir()
    alias = store.root / "alias"
    alias.symlink_to(metadata, target_is_directory=True)
    with pytest.raises(UnsafePathError, match="reserved"):
        store.path_for("alias/entry")
    with pytest.raises(UnsafePathError, match="reserved"):
        store.create_entry(alias / "entry.bm", "new")
    assert not list(metadata.iterdir())


def test_tag_rewrite_preserves_quotes_unknown_metadata_and_body(tmp_path):
    store = Store(tmp_path / "store")
    store.create()
    path = store.path_for("reading/example")
    tags = ["O'Reilly", 'say"hello', "with,comma", r"C:\reading", "café"]
    meta = {"url": "https://example.com", "tags": tags, "x-extra": "first\nsecond"}
    store.create_entry(path, build_text(meta, "unchanged notes\n"))

    assert main(["--store", str(store.root), "tag", "add", "reading/example", "new"]) == 0
    rewritten, body = load_entry(path)
    assert rewritten["tags"] == sorted([*tags, "new"])
    assert rewritten["x-extra"] == meta["x-extra"]
    assert body == "unchanged notes\n"
    assert len(read_backup(build_backup(store))) == 1


def test_literal_legacy_backslash_tag_survives_cli_rewrite_and_backup_restore(tmp_path):
    source = Store(tmp_path / "source")
    source.create()
    path = source.path_for("entry")
    path.write_bytes(
        b'---\nurl: https://example.com\ntags: ["C:\\new folder", "\\u0041"]\n---\nnotes\n'
    )
    expected = [r"C:\new folder", r"\u0041"]
    assert load_entry(path)[0]["tags"] == expected
    assert main(["--store", str(source.root), "tag", "add", "entry", "added"]) == 0
    assert load_entry(path)[0]["tags"] == sorted([*expected, "added"])
    assert b"tags: json:[" in path.read_bytes()

    archive = tmp_path / "backup.json"
    archive.write_text(build_backup(source), encoding="utf-8")
    restored = tmp_path / "restored"
    assert main(["--store", str(restored), "import", str(archive)]) == 0
    assert (restored / "entry.bm").read_bytes() == path.read_bytes()
    assert load_entry(restored / "entry.bm")[0]["tags"] == sorted([*expected, "added"])


def test_browser_reserved_folder_is_invalid_in_preview_and_import(tmp_path, capsys):
    source = tmp_path / "browser.html"
    source.write_text(
        '<DL><DT><H3>.git</H3><DL><DT><A HREF="https://example.com">Page</A></DL></DL>',
        encoding="utf-8",
    )
    store = tmp_path / "store"
    assert main(["--store", str(store), "import", str(source), "--dry-run"]) == 0
    preview = capsys.readouterr()
    assert "invalid 1" in preview.out
    assert not store.exists()
    assert main(["--store", str(store), "import", str(source)]) == 0
    assert not list(store.rglob("*.bm"))
