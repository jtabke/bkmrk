"""Data models and constants for the bookmark manager."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Mapping, Optional

FILE_EXT = ".bm"


def default_store(environ: Optional[Mapping[str, str]] = None) -> Path:
    """Return the configured default store, resolving the environment at call time."""
    values = os.environ if environ is None else environ
    return Path(values.get("BOOKMARKS_DIR", str(Path.home() / ".bookmarks.d")))


FM_START = "---\n"
FM_END = "---\n"


@dataclass
class Bookmark:
    """Represents a bookmark entry."""

    url: str
    title: str = ""
    tags: List[str] = field(default_factory=list)
    created: Optional[str] = None
    modified: Optional[str] = None
    notes: str = ""
