"""Pure Netscape bookmark HTML parsing and rendering."""

import html
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple

from .models import Metadata, metadata_tags, metadata_text
from .utils import parse_iso, to_epoch

NETSCAPE_HEADER = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<!-- This is an automatically generated file. -->
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
"""
NETSCAPE_FOOTER = "</DL><p>\n"


@dataclass
class _FolderNode:
    """Keep folder names separate from the node's bookmark collection."""

    children: dict[str, "_FolderNode"] = field(default_factory=dict)
    bookmarks: list[str] = field(default_factory=list)


class _NetscapeParser(HTMLParser):
    """Collect Netscape bookmarks while tolerating HTML layout variations."""

    def __init__(self, default_created: Optional[str]) -> None:
        super().__init__(convert_charrefs=True)
        self.entries: List[Tuple[str, Metadata]] = []
        self.invalid_count = 0
        self.folder_stack: List[str] = []
        self.default_created = default_created
        self._folder_text: Optional[List[str]] = None
        self._bookmark: Optional[Dict[str, Optional[str]]] = None
        self._bookmark_title: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag == "h3":
            self._folder_text = []
        elif tag == "a":
            if self._bookmark is not None:
                self._finish_bookmark()
            attributes = {name: value for name, value in attrs}
            self._bookmark = {
                "url": attributes.get("href"),
                "tags": attributes.get("tags"),
                "add_date": attributes.get("add_date"),
            }
            self._bookmark_title = []

    def handle_data(self, data: str) -> None:
        if self._folder_text is not None:
            self._folder_text.append(data)
        if self._bookmark is not None:
            self._bookmark_title.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "h3" and self._folder_text is not None:
            self.folder_stack.append("".join(self._folder_text).strip())
            self._folder_text = None
        elif tag == "a" and self._bookmark is not None:
            self._finish_bookmark()
        elif tag == "dl" and self.folder_stack:
            self.folder_stack.pop()

    def finish(self) -> None:
        """Finalize a truncated trailing bookmark after all input is consumed."""
        if self._bookmark is not None:
            self._finish_bookmark()

    def _finish_bookmark(self) -> None:
        bookmark = self._bookmark
        assert bookmark is not None
        self._bookmark = None
        url = bookmark["url"]
        if url is None:
            self.invalid_count += 1
            return

        raw_tags = bookmark["tags"] or ""
        tags = [tag.strip() for tag in raw_tags.split(",") if tag.strip()]
        meta: Metadata = {
            "url": url,
            "title": "".join(self._bookmark_title).strip(),
            "tags": tags,
        }
        if self.default_created is not None:
            meta["created"] = self.default_created

        add_date = bookmark["add_date"]
        if add_date and add_date.isascii() and add_date.isdigit():
            try:
                meta["created"] = datetime.fromtimestamp(int(add_date), tz=timezone.utc).isoformat()
            except (OverflowError, OSError, ValueError):
                pass

        path = "/".join(self.folder_stack) if self.folder_stack else ""
        self.entries.append((path, meta))


def build_netscape_tree(entries: List[Tuple[str, Metadata]]) -> str:
    """Build Netscape HTML with folder hierarchy from ``(path, metadata)`` entries."""

    def build_html(node: _FolderNode) -> str:
        parts = list(node.bookmarks)
        for name, child in sorted(node.children.items()):
            folder_name = html.escape(name, quote=True)
            parts.append(f"<DT><H3>{folder_name}</H3>\n<DL><p>\n")
            parts.append(build_html(child))
            parts.append("</DL><p>\n")
        return "".join(parts)

    root = _FolderNode()
    for path, meta in entries:
        parts = path.split("/")
        current = root
        for part in parts[:-1]:
            if part not in current.children:
                current.children[part] = _FolderNode()
            current = current.children[part]
        bookmarks = current.bookmarks
        add_date = (
            to_epoch(
                parse_iso(metadata_text(meta, "created"))
                or parse_iso(metadata_text(meta, "modified"))
            )
            or ""
        )
        tags = html.escape(",".join(str(tag) for tag in metadata_tags(meta)), quote=True)
        title = html.escape(
            str(metadata_text(meta, "title") or metadata_text(meta, "url") or ""), quote=True
        )
        url = html.escape(str(metadata_text(meta, "url") or ""), quote=True)
        bookmarks.append(f'<DT><A HREF="{url}" ADD_DATE="{add_date}" TAGS="{tags}">{title}</A>\n')

    return build_html(root)


def parse_netscape_html(
    text: str, *, default_created: Optional[str] = None
) -> List[Tuple[str, Metadata]]:
    """Parse Netscape HTML into ``(folder path, metadata)`` bookmark entries.

    ``default_created`` is supplied by the application boundary for imports
    without an ``ADD_DATE`` attribute, keeping this parser deterministic.
    ``HTMLParser`` handles compact/minified input, mixed-case markup, and
    entity decoding while remaining deliberately best-effort for malformed HTML.
    """
    return parse_netscape_document(text, default_created=default_created)[0]


def parse_netscape_document(
    text: str, *, default_created: Optional[str] = None
) -> Tuple[List[Tuple[str, Metadata]], int]:
    """Return parsed entries and the count of anchors missing an href attribute."""
    parser = _NetscapeParser(default_created)
    parser.feed(text)
    parser.close()
    parser.finish()
    return parser.entries, parser.invalid_count
