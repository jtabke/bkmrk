"""Tests for the concrete filesystem store boundary."""

from unittest.mock import patch

import pytest

from bm.commands import cmd_dedupe
from bm.errors import AmbiguousEntryError, ConflictError, UnsafePathError
from bm.io import build_text, load_entry
from bm.store import Store


def _write_entry(path, *, url="https://example.com", title="", body=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        build_text({"url": url, "title": title}, body),
        encoding="utf-8",
    )


def test_store_resolves_by_id_and_path(tmp_path):
    store = Store(tmp_path / "store")
    store.create()
    path = store.path_for("test")
    _write_entry(path, url="https://example.com")

    from bm.utils import rid

    assert store.resolve("test") == path
    assert store.resolve(rid("https://example.com")) == path
    assert store.resolve("does-not-exist") is None


def test_store_id_match_wins_over_fuzzy_path_match(tmp_path):
    store = Store(tmp_path / "store")
    store.create()
    first = store.path_for("page1")
    second = store.path_for("page1-suffix")
    _write_entry(first, url="https://example.com/page1")
    _write_entry(second, url="https://different.com/page1-suffix")

    from bm.utils import rid

    assert store.resolve(rid("https://example.com/page1")) == first


def test_store_reports_ambiguous_fuzzy_resolution(tmp_path):
    store = Store(tmp_path / "store")
    store.create()
    _write_entry(store.path_for("prefix-suffix"), url="https://example1.com")
    _write_entry(store.path_for("other-suffix"), url="https://example2.com")

    with pytest.raises(AmbiguousEntryError) as exc_info:
        store.resolve("suffix")
    message = str(exc_info.value)
    assert "ambiguous" in message
    assert "prefix-suffix" in message
    assert "other-suffix" in message


def test_store_path_and_snapshot_boundary(tmp_path):
    store = Store(tmp_path / "store")
    store.create()
    path = store.path_for("dev/example")
    _write_entry(path, title="Example")

    entries = list(store.iter_entries(snapshot=True))

    assert len(entries) == 1
    assert entries[0].path == path
    assert entries[0].relative_path.as_posix() == "dev/example"
    assert entries[0].snapshot == path.read_bytes()
    assert entries[0].meta["title"] == "Example"

    with pytest.raises(UnsafePathError):
        store.path_for("../outside")


def test_store_iteration_and_fuzzy_resolution_skip_outward_symlinks(tmp_path):
    store = Store(tmp_path / "store")
    store.create()
    outside = tmp_path / "outside.bm"
    _write_entry(outside, url="https://outside.example.com")
    link = store.root / "outside-link.bm"
    link.symlink_to(outside)

    assert list(store.iter_entries(meta_only=True)) == []
    assert store.resolve("outside") is None


def test_prune_with_relative_root_never_removes_store_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = Store("store")
    store.create()
    nested = store.root.resolve() / "nested"
    nested.mkdir()

    store.prune_empty_dirs(nested)

    assert store.root.is_dir()
    assert not nested.exists()


def test_store_mutations_reject_stale_snapshots(tmp_path):
    store = Store(tmp_path / "store")
    store.create()
    source = store.path_for("source")
    destination = store.path_for("destination")
    _write_entry(source)
    snapshot = store.snapshot(source)
    source.write_text("external change\n", encoding="utf-8")

    with pytest.raises(ConflictError):
        store.write(source, "replacement", expected=snapshot)
    with pytest.raises(ConflictError):
        store.delete(source, expected=snapshot)
    with pytest.raises(ConflictError):
        store.move(source, destination, expected=snapshot)

    assert source.read_text(encoding="utf-8") == "external change\n"
    assert not destination.exists()


def test_dedupe_conflict_preserves_newer_redundant_entry(tmp_path):
    store_path = tmp_path / "store"
    store_path.mkdir()
    survivor = store_path / "one.bm"
    redundant = store_path / "two.bm"
    _write_entry(survivor, title="One", body="short")
    _write_entry(redundant, title="Two", body="longer")

    real_verify = Store.verify
    verify_calls = 0

    def interfere(current_store, path, expected):
        nonlocal verify_calls
        verify_calls += 1
        if verify_calls == 1:
            redundant.write_text("newer external content\n", encoding="utf-8")
        return real_verify(current_store, path, expected)

    args = type("Args", (), {"store": str(store_path), "dry_run": False, "json": False})()
    with patch.object(Store, "verify", interfere), pytest.raises(ConflictError):
        cmd_dedupe(args)

    assert verify_calls >= 1
    assert survivor.exists()
    assert redundant.read_text(encoding="utf-8") == "newer external content\n"
    # No partial rewrite should have replaced the survivor before all snapshots
    # were verified.
    assert load_entry(survivor)[0]["title"] == "One"


def test_dedupe_late_conflict_preserves_newer_file_and_reports_partial_progress(tmp_path):
    store_path = tmp_path / "store"
    store_path.mkdir()
    survivor = store_path / "a-survivor.bm"
    first_redundant = store_path / "b-first.bm"
    late_conflict = store_path / "c-late.bm"
    _write_entry(survivor, title="Survivor", body="longest body wins")
    _write_entry(first_redundant, title="First")
    _write_entry(late_conflict, title="Late")

    real_delete = Store.delete
    delete_calls = 0
    conflicted_path = None

    def interfere(current_store, path, expected=None):
        nonlocal delete_calls, conflicted_path
        delete_calls += 1
        if delete_calls == 2:
            conflicted_path = path
            path.write_text("newer external content\n", encoding="utf-8")
        return real_delete(current_store, path, expected)

    args = type("Args", (), {"store": str(store_path), "dry_run": False, "json": False})()
    with patch.object(Store, "delete", interfere), pytest.raises(ConflictError):
        cmd_dedupe(args)

    assert survivor.exists()
    assert conflicted_path is not None
    assert conflicted_path.read_text(encoding="utf-8") == "newer external content\n"
    remaining_redundant = [path for path in (first_redundant, late_conflict) if path.exists()]
    assert remaining_redundant == [conflicted_path]
