"""Shared pytest fixtures for the bm test suite."""

import argparse
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
