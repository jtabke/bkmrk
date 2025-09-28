"""Data models and constants for the bookmark manager."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_STORE = Path(os.environ.get("BOOKMARKS_DIR", str(Path.home() / ".bookmarks.d")))
FILE_EXT = ".bm"
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

    def to_meta(self) -> Dict[str, Any]:
        """Convert to metadata dict for front matter."""
        meta = {
            "url": self.url,
            "title": self.title,
            "tags": self.tags,
            "created": self.created,
            "modified": self.modified,
            "notes": self.notes,
        }
        return {k: v for k, v in meta.items() if v not in (None, "", [])}
