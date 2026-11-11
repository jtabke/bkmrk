"""Open parsed metadata, typed field access, and runtime configuration."""

import os
from pathlib import Path
from typing import Mapping, Optional

FILE_EXT = ".bm"
FM_START = "---\n"
FM_END = "---\n"

# Parsing retains unknown keys as text; tags are the one structured field.
type MetadataValue = str | list[str]
type Metadata = dict[str, MetadataValue]


def metadata_text(meta: Mapping[str, MetadataValue], key: str) -> str:
    """Read a text field without interpreting or changing the open metadata mapping."""
    value = meta.get(key, "")
    return value if isinstance(value, str) else ""


def metadata_tags(meta: Mapping[str, MetadataValue]) -> list[str]:
    """Read parsed tags in their original order; copy before mutating them."""
    value = meta.get("tags", [])
    return value if isinstance(value, list) else []


def default_store(environ: Optional[Mapping[str, str]] = None) -> Path:
    """Return the configured default store, resolving the environment at call time."""
    values = os.environ if environ is None else environ
    return Path(values.get("BOOKMARKS_DIR", str(Path.home() / ".bookmarks.d")))
