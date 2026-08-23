"""Pure Netscape bookmark HTML parsing and rendering."""

import html
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .utils import parse_iso, to_epoch

NETSCAPE_HEADER = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<!-- This is an automatically generated file. -->
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
"""
NETSCAPE_FOOTER = "</DL><p>\n"

_RE_NETSCAPE_FOLDER = re.compile(r"<DT>\s*<H3\b[^>]*>(.*?)</H3>", re.I)
_RE_NETSCAPE_BOOKMARK = re.compile(r"<DT>\s*<A\b[^>]*HREF=\"([^\"]+)\"[^>]*>(.*?)</A>", re.I)
_RE_NETSCAPE_TAGS = re.compile(r'\bTAGS="([^\"]*)"', re.I)
_RE_NETSCAPE_ADDDATE = re.compile(r'\bADD_DATE="(\d+)"')
_RE_NETSCAPE_DLEND = re.compile(r"</DL\b", re.I)
_RE_HTML_TAG = re.compile(r"<[^>]+>")


def build_netscape_tree(entries: List[Tuple[str, Dict[str, Any]]]) -> str:
    """Build Netscape HTML with folder hierarchy from ``(path, metadata)`` entries."""

    def build_html(node: Dict[str, Any]) -> str:
        output = ""
        for bookmark in node.get("__bookmarks__", []):
            output += bookmark
        for key, value in sorted(node.items()):
            if key == "__bookmarks__":
                continue
            if isinstance(value, dict):
                folder_name = html.escape(str(key), quote=True)
                output += f"<DT><H3>{folder_name}</H3>\n<DL><p>\n"
                output += build_html(value)
                output += "</DL><p>\n"
        return output

    root: Dict[str, Any] = {}
    for path, meta in entries:
        parts = path.split("/")
        current = root
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        bookmarks = current.setdefault("__bookmarks__", [])
        add_date = to_epoch(parse_iso(meta.get("created")) or parse_iso(meta.get("modified"))) or ""
        tags = html.escape(",".join(str(tag) for tag in meta.get("tags", [])), quote=True)
        title = html.escape(str(meta.get("title") or meta.get("url") or ""), quote=True)
        url = html.escape(str(meta.get("url") or ""), quote=True)
        bookmarks.append(f'<DT><A HREF="{url}" ADD_DATE="{add_date}" TAGS="{tags}">{title}</A>\n')

    return build_html(root)


def parse_netscape_html(
    text: str, *, default_created: Optional[str] = None
) -> List[Tuple[str, Dict[str, Any]]]:
    """Parse Netscape HTML into ``(folder path, metadata)`` bookmark entries.

    ``default_created`` is supplied by the application boundary for imports
    without an ``ADD_DATE`` attribute, keeping this parser deterministic.
    """
    entries: List[Tuple[str, Dict[str, Any]]] = []
    folder_stack: List[str] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        folder = _RE_NETSCAPE_FOLDER.search(line)
        if folder:
            folder_stack.append(html.unescape(folder.group(1)))
            i += 1
            continue

        bookmark = _RE_NETSCAPE_BOOKMARK.search(line)
        if bookmark:
            url = html.unescape(bookmark.group(1))
            title_html = bookmark.group(2)
            title = html.unescape(_RE_HTML_TAG.sub("", title_html))
            tag_match = _RE_NETSCAPE_TAGS.search(line)
            raw_tags = html.unescape(tag_match.group(1)) if tag_match else ""
            tags = [tag.strip() for tag in raw_tags.split(",") if tag.strip()]
            meta: Dict[str, Any] = {
                "url": url,
                "title": title.strip(),
                "tags": tags,
            }
            if default_created is not None:
                meta["created"] = default_created
            add_date = _RE_NETSCAPE_ADDDATE.search(line)
            if add_date:
                try:
                    meta["created"] = datetime.fromtimestamp(
                        int(add_date.group(1)), tz=timezone.utc
                    ).isoformat()
                except (OverflowError, OSError, ValueError):
                    pass
            entries.append(("/".join(folder_stack) if folder_stack else "", meta))
            i += 1
            continue

        if _RE_NETSCAPE_DLEND.match(line):
            if folder_stack:
                folder_stack.pop()
            i += 1
            continue
        i += 1
    return entries
