"""Shared pytest fixtures for the bm test suite."""

import argparse
import sys
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def store(tmp_path: Path) -> Path:
    """Return a fresh store directory ready for use by commands."""
    s = tmp_path / "store"
    s.mkdir()
    return s


@pytest.fixture
def interactive_terminal(monkeypatch, capsys):
    """Simulate terminal availability across pytest's capture-stream replacements."""
    monkeypatch.setattr(type(sys.stdin), "isatty", lambda self: True)
    monkeypatch.setattr(type(sys.stderr), "isatty", lambda self: True)


@pytest.fixture
def bookmark_factory():
    """Return a CLI test entry factory that can target multiple disposable stores."""

    def _make(
        store: Path,
        name: str = "guide.bm",
        url: str = "https://example.com",
        title: str = "Python async guide",
    ) -> Path:
        path = store / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\nurl: {url}\ntitle: {title}\ntags: [dev]\ncreated: 2025-01-01\n---\n",
            encoding="utf-8",
        )
        return path

    return _make


@pytest.fixture
def args_factory(store: Path):
    """Return a factory that builds an argparse.Namespace defaulting to `store`.

    Use in place of MagicMock() so missing attributes raise AttributeError
    instead of silently returning truthy sentinels — surfacing latent bugs
    where the code under test reads an arg the test forgot to set.
    """

    def _make(**overrides: Any) -> argparse.Namespace:
        defaults: dict = {"store": str(store)}
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    return _make


@pytest.fixture
def write_bm(store: Path):
    """Return a helper that writes a bookmark file under the store."""

    def _make(rel: str, *, url: str = "", title: str = "", tags=None, body: str = "") -> Path:
        target = store / rel
        if not target.suffix:
            target = target.with_suffix(".bm")
        target.parent.mkdir(parents=True, exist_ok=True)
        lines = ["---\n"]
        if url:
            lines.append(f"url: {url}\n")
        if title:
            lines.append(f"title: {title}\n")
        if tags:
            lines.append(f"tags: [{', '.join(tags)}]\n")
        lines.append("---\n")
        if body:
            lines.append(body if body.endswith("\n") else body + "\n")
        target.write_text("".join(lines), encoding="utf-8")
        return target

    return _make
