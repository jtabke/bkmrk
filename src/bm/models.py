"""Constants and runtime configuration for the bookmark manager."""

import os
from pathlib import Path
from typing import Mapping, Optional

FILE_EXT = ".bm"
FM_START = "---\n"
FM_END = "---\n"


def default_store(environ: Optional[Mapping[str, str]] = None) -> Path:
    """Return the configured default store, resolving the environment at call time."""
    values = os.environ if environ is None else environ
    return Path(values.get("BOOKMARKS_DIR", str(Path.home() / ".bookmarks.d")))
