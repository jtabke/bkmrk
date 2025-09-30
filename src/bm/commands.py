"""Command implementations for the bookmark manager."""

import html
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Set, Tuple
from urllib.parse import urlparse

from .io import atomic_write, build_text, load_entry, parse_front_matter
from .models import DEFAULT_STORE, FILE_EXT
from .utils import (
    _launch_editor,
    _reject_unsafe,
    create_slug_from_url,
    die,
    id_to_path,
    is_relative_to,
    iso_now,
    normalize_slug,
    normalize_url_for_compare,
    parse_iso,
    rid,
    to_epoch,
)


def cmd_init(args) -> None:
    """Initialize a new bookmark store.

    Creates the store directory and optionally initializes a git repository.

    Args:
        args: Parsed command line arguments.
    """
    store = Path(args.store or DEFAULT_STORE)
    store.mkdir(parents=True, exist_ok=True)
    print(f"Initialized store at: {store}")
    if args.git:
        if (store / ".git").exists():
            print("Git repo already exists.")
        else:
            subprocess.run(["git", "init"], cwd=store)
            print("Initialized git repository.")
    readme = store / "README.txt"
    if not readme.exists():
        readme.write_text(
            textwrap.dedent(f"""\
            bm store
            =========
            • One bookmark per {FILE_EXT} file.
            • Organize via folders (act as tags/namespaces).
            • File format: front matter + body notes.

            Fields:
              url: https://example.com
              title: Example
              tags: [sample, demo]
              created: {iso_now()}

            Body after the second '---' is freeform notes.
        """),
            encoding="utf-8",
        )


def cmd_add(args) -> None:
    """Add a new bookmark."""
    store = Path(args.store or DEFAULT_STORE)
    if not store.exists():
        die(f"store not found: {store}. Run `bm init` first.")
    url = args.url.strip()
    slug = args.id or create_slug_from_url(url)
    if args.path:
        slug = f"{normalize_slug(args.path)}/{normalize_slug(slug)}"
    slug = _reject_unsafe(slug)
    fpath = id_to_path(store, slug)
    if not is_relative_to(fpath, store):
        die("destination escapes store")
    if fpath.exists() and not args.force:
        die(f"bookmark exists: {slug} (use --force to overwrite)")
    fpath.parent.mkdir(parents=True, exist_ok=True)

    meta = {
        "url": url,
        "title": args.name or "",
        "tags": [t.strip() for t in (args.tags or "").split(",") if t.strip()],
        "created": iso_now(),
    }
    body = (args.description or "").rstrip()
    if body:
        body += "\n"

    if args.edit:
        # Pre-populate a template and open $EDITOR
        template = build_text(meta, body)
        tmp = (
            Path(os.environ.get("TMPDIR") or os.environ.get("TEMP") or "/tmp")
            / f"bm-{os.getpid()}.bm"
        )
        tmp.write_text(template, encoding="utf-8")
        _launch_editor(tmp)
        meta2, body2 = parse_front_matter(tmp.read_text(encoding="utf-8", errors="replace"))
        try:
            tmp.unlink()
        except Exception:
            pass
        # Merge back (keep created)
        meta.update({k: v for k, v in meta2.items() if k != "created"})
        body = body2

    atomic_write(fpath, build_text(meta, body))
    print(rid(meta.get("url", "")))


def cmd_show(args) -> None:
    """Show a bookmark entry."""
    store = Path(args.store or DEFAULT_STORE)
    p = resolve_id_or_path(store, args.id)
    if not p:
        die("not found")
    assert p is not None
    rel = p.relative_to(store).with_suffix("")
    print(f"# {rel}")
    meta, body = load_entry(p)
    for k in ["url", "title", "tags", "created", "modified"]:
        if k in meta and meta[k]:
            if k == "tags":
                print(f"{k}: {', '.join(meta[k])}")
            else:
                print(f"{k}: {meta[k]}")
    if body.strip():
        print("\n" + body.rstrip())


def cmd_open(args) -> None:
    """Open bookmark in browser."""
    store = Path(args.store or DEFAULT_STORE)
    p = resolve_id_or_path(store, args.id)
    if not p:
        die("not found")
    meta, _ = load_entry(p)
    url = meta.get("url")
    if not url:
        die("no url in entry")
    ok = webbrowser.open(url)
    print(url)
    if not ok:
        print("bm: warning: system did not acknowledge opening browser", file=sys.stderr)


def _iter_entries(store: Path) -> Generator[Tuple[Path, Path, Dict[str, Any], str], None, None]:
    """Iterate over all entries."""
    for p in sorted(store.rglob(f"*{FILE_EXT}")):
        rel = p.relative_to(store).with_suffix("")
        meta, body = load_entry(p)
        yield p, rel, meta, body


def _matches_tag(rel, meta, tag):
    if not tag:
        return True
    segs = set(rel.parts[:-1])
    header_tags = set(meta.get("tags", []))
    return tag in segs or tag in header_tags


def _matches_host(meta, want_host):
    if not want_host:
        return True
    host = urlparse(meta.get("url", "")).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    hq = want_host[4:] if want_host.startswith("www.") else want_host
    return host == hq


def _matches_since(meta, since_dt):
    if not since_dt:
        return True
    ts = parse_iso(meta.get("created")) or parse_iso(meta.get("modified"))
    return ts and ts >= since_dt


def _matches_path(rel, path_prefix):
    if not path_prefix:
        return True
    # Normalize path prefix (remove leading/trailing slashes)
    path_prefix = path_prefix.strip("/")
    if not path_prefix:
        return True
    # Check if relative path starts with the prefix
    rel_str = str(rel)
    return rel_str.startswith(path_prefix + "/") or rel_str == path_prefix


def _entry_score(entry: Dict[str, Any]) -> Tuple[int, int, float, str]:
    body_len = len(entry["body"].strip())
    title_len = len(entry["meta"].get("title", "").strip())
    created_dt = parse_iso(entry["meta"].get("created")) or parse_iso(entry["meta"].get("modified"))
    if created_dt and created_dt.tzinfo is None:
        created_dt = created_dt.replace(tzinfo=timezone.utc)
    created_ts = created_dt.timestamp() if created_dt else float("inf")
    return (-body_len, -title_len, created_ts, str(entry["rel"]))


def _select_survivor(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    return min(entries, key=_entry_score)


def _prune_empty_dirs(store: Path, start: Path) -> None:
    cur = start
    while cur != store and cur.exists() and not any(cur.iterdir()):
        cur.rmdir()
        cur = cur.parent


def _normalize_dt(dt: Optional[datetime]) -> Optional[datetime]:
    if dt and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _earliest_dt(current: Optional[datetime], candidate: Optional[datetime]) -> Optional[datetime]:
    candidate = _normalize_dt(candidate)
    if candidate is None:
        return current
    if current is None or candidate < current:
        return candidate
    return current


def _latest_dt(current: Optional[datetime], candidate: Optional[datetime]) -> Optional[datetime]:
    candidate = _normalize_dt(candidate)
    if candidate is None:
        return current
    if current is None or candidate > current:
        return candidate
    return current


def _collect_group_stats(
    entries: List[Dict[str, Any]],
) -> Tuple[Set[str], Optional[datetime], Optional[datetime], List[str]]:
    tags_union: Set[str] = set()
    earliest_created: Optional[datetime] = None
    latest_modified: Optional[datetime] = None
    title_candidates: List[str] = []

    for entry in entries:
        meta = entry["meta"]
        tags_union.update(t.strip() for t in meta.get("tags", []) if t.strip())
        tags_union.update(seg for seg in entry["rel"].parts[:-1] if seg)

        title = meta.get("title", "").strip()
        if title:
            title_candidates.append(title)

        earliest_created = _earliest_dt(earliest_created, parse_iso(meta.get("created")))
        latest_modified = _latest_dt(latest_modified, parse_iso(meta.get("modified")))

    return tags_union, earliest_created, latest_modified, title_candidates


def _collect_body_parts(
    entries: List[Dict[str, Any]], survivor: Dict[str, Any]
) -> Tuple[List[str], bool]:
    base_body = survivor["body"].rstrip()
    parts: List[str] = [base_body] if base_body else []
    notes_appended = False

    for entry in entries:
        if entry is survivor:
            continue
        extra_body = entry["body"].rstrip()
        if extra_body:
            notes_appended = True
            parts.append(f"[Merged from {entry['rel']}]\n{extra_body}".rstrip())

    return parts, notes_appended


def _join_body_parts(parts: List[str]) -> str:
    clean = [part for part in parts if part]
    if not clean:
        return ""
    merged = "\n\n".join(clean)
    return merged.rstrip() + "\n"


def _merge_entry_group(
    entries: List[Dict[str, Any]], survivor: Dict[str, Any]
) -> Tuple[Dict[str, Any], str, List[str], bool, Optional[datetime], Optional[datetime]]:
    merged_meta = dict(survivor["meta"])
    tags_union, earliest_created, latest_modified, title_candidates = _collect_group_stats(entries)

    if title_candidates and not merged_meta.get("title"):
        title_candidates.sort(key=len, reverse=True)
        merged_meta["title"] = title_candidates[0]

    if earliest_created:
        merged_meta["created"] = earliest_created.isoformat()

    if latest_modified:
        merged_meta["modified"] = latest_modified.isoformat()

    body_parts, notes_appended = _collect_body_parts(entries, survivor)
    tags = sorted(t for t in tags_union if t)
    merged_body = _join_body_parts(body_parts)

    return merged_meta, merged_body, tags, notes_appended, earliest_created, latest_modified


def _build_row(rel, meta, ts):
    url = meta.get("url", "")
    return {
        "id": rid(url),
        "path": str(rel),
        "title": meta.get("title", ""),
        "url": url,
        "tags": meta.get("tags", []),
        "created": meta.get("created", ""),
        "modified": meta.get("modified", ""),
        "_sort": ts or datetime.min.replace(tzinfo=timezone.utc),
    }


def _collect_rows(store: Path, args) -> List[dict]:
    rows = []
    since_dt = parse_iso(args.since) if args.since else None
    want_host = (args.host or "").lower()
    want_path = getattr(args, "path", None)
    if want_path and isinstance(want_path, str):
        want_path = want_path.strip("/")
    else:
        want_path = ""
    for _, rel, meta, _ in _iter_entries(store):
        if not _matches_tag(rel, meta, args.tag):
            continue
        if not _matches_host(meta, want_host):
            continue
        if not _matches_path(rel, want_path):
            continue
        ts = parse_iso(meta.get("created")) or parse_iso(meta.get("modified"))
        if not _matches_since(meta, since_dt):
            continue
        rows.append(_build_row(rel, meta, ts))
    rows.sort(key=lambda r: r["_sort"], reverse=True)
    for r in rows:
        r.pop("_sort", None)
    return rows


def _output_rows(rows: List[dict], args):
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
    elif args.jsonl:
        for r in rows:
            print(json.dumps(r, ensure_ascii=False))
    else:
        for r in rows:
            t = f" — {r['title']}" if r["title"] else ""
            u = f" <{r['url']}>" if r["url"] else ""
            print(f"{r['id']}  {r['path']}{t}{u}")


def cmd_list(args) -> None:
    """List bookmarks."""
    store = Path(args.store or DEFAULT_STORE)
    if not store.exists():
        die(f"store not found: {store}")
    rows = _collect_rows(store, args)
    _output_rows(rows, args)


def cmd_search(args) -> None:
    """Search bookmarks."""
    store = Path(args.store or DEFAULT_STORE)
    q = args.query.lower()
    want_path = getattr(args, "path", None)
    if want_path and isinstance(want_path, str):
        want_path = want_path.strip("/")
    else:
        want_path = ""
    hits = []
    for _, rel, meta, body in _iter_entries(store):
        if not _matches_path(rel, want_path):
            continue
        blob = "\n".join(
            [
                meta.get("title", ""),
                meta.get("url", ""),
                " ".join(meta.get("tags", [])),
                body,
            ]
        ).lower()
        if all(term in blob for term in q.split()):
            url = meta.get("url", "")
            ts = parse_iso(meta.get("created")) or parse_iso(meta.get("modified"))
            hits.append(
                {
                    "id": rid(url),
                    "path": str(rel),
                    "title": meta.get("title", ""),
                    "url": url,
                    "tags": meta.get("tags", []),
                    "created": meta.get("created", ""),
                    "modified": meta.get("modified", ""),
                    "_sort": ts or datetime.min.replace(tzinfo=timezone.utc),
                }
            )
    hits.sort(key=lambda r: r["_sort"], reverse=True)
    for r in hits:
        r.pop("_sort", None)

    if args.json:
        print(json.dumps(hits, ensure_ascii=False))
    elif args.jsonl:
        for r in hits:
            print(json.dumps(r, ensure_ascii=False))
    else:
        for r in hits:
            print(f"{r['id']}  {r['path']}")


def cmd_edit(args) -> None:
    """Edit bookmark with editor."""
    store = Path(args.store or DEFAULT_STORE)
    p = resolve_id_or_path(store, args.id)
    if not p:
        die("not found")
    _launch_editor(p)
    # bump modified timestamp
    meta, body = load_entry(p)
    meta["modified"] = iso_now()
    atomic_write(p, build_text(meta, body))


def cmd_rm(args) -> None:
    """Remove bookmark."""
    store = Path(args.store or DEFAULT_STORE)
    p = resolve_id_or_path(store, args.id)
    if not p:
        die("not found")
    assert p is not None
    p.unlink()
    # prune empty dirs
    d = p.parent
    while d != store and not any(d.iterdir()):
        d.rmdir()
        d = d.parent


def cmd_mv(args) -> None:
    """Move/rename bookmark."""
    store = Path(args.store or DEFAULT_STORE)
    src = resolve_id_or_path(store, args.src)
    if not src:
        die("source not found")
    dst_slug = normalize_slug(args.dst)
    dst_slug = _reject_unsafe(dst_slug)
    dst = id_to_path(store, dst_slug)
    if not is_relative_to(dst, store):
        die("destination escapes store")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and not args.force:
        die("destination exists (use --force)")
    shutil.move(str(src), str(dst))
    print(dst.relative_to(store).with_suffix(""))


def cmd_tags(args) -> None:
    """List all tags."""
    store = Path(args.store or DEFAULT_STORE)
    folder_tags = set()
    header_tags = set()
    for _, rel, meta, _ in _iter_entries(store):
        folder_tags.update(rel.parts[:-1])
        header_tags.update(t.strip() for t in meta.get("tags", []) if t.strip())
    all_tags = sorted(folder_tags | header_tags)
    for t in all_tags:
        print(t)


def cmd_dirs(args) -> None:
    """List known directory prefixes."""
    store = Path(args.store or DEFAULT_STORE)
    dirs = set()
    for _, rel, _, _ in _iter_entries(store):
        # Add all parent directories
        parts = rel.parts
        for i in range(1, len(parts)):
            dirs.add("/".join(parts[:i]))
    all_dirs = sorted(dirs)
    if args.json:
        print(json.dumps(all_dirs, ensure_ascii=False))
    else:
        for d in all_dirs:
            print(d)


def _group_entries_by_url(store: Path) -> Dict[str, List[Dict[str, Any]]]:
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for path, rel, meta, body in _iter_entries(store):
        url = meta.get("url", "").strip()
        key = normalize_url_for_compare(url)
        if not key:
            continue
        buckets.setdefault(key, []).append(
            {"path": path, "rel": rel, "meta": dict(meta), "body": body}
        )
    return buckets


def _write_merged_entry(
    survivor: Dict[str, Any], merged_meta: Dict[str, Any], merged_body: str, tags: List[str]
) -> None:
    merged_meta_for_write = dict(merged_meta)
    merged_meta_for_write["tags"] = tags
    merged_meta_for_write["modified"] = iso_now()
    atomic_write(survivor["path"], build_text(merged_meta_for_write, merged_body))
    survivor["meta"] = merged_meta_for_write
    survivor["body"] = merged_body


def _remove_group_entries(store: Path, entries: List[Dict[str, Any]]) -> None:
    for entry in entries:
        try:
            entry["path"].unlink()
        except FileNotFoundError:
            continue
        _prune_empty_dirs(store, entry["path"].parent)


def _process_duplicate_group(
    store: Path, canonical: str, group: List[Dict[str, Any]], dry_run: bool
) -> Dict[str, Any]:
    survivor = _select_survivor(group)
    (
        merged_meta,
        merged_body,
        tags,
        notes_appended,
        earliest_created,
        latest_modified,
    ) = _merge_entry_group(group, survivor)

    removed_entries = [entry for entry in group if entry is not survivor]
    action: Dict[str, Any] = {
        "canonical_url": canonical,
        "kept": str(survivor["rel"]),
        "removed": [str(entry["rel"]) for entry in removed_entries],
        "tags": tags,
        "notes_appended": notes_appended,
        "total": len(group),
    }

    if earliest_created:
        action["created"] = earliest_created.isoformat()
    if latest_modified:
        action["latest_modified"] = latest_modified.isoformat()
    if dry_run:
        action["dry_run"] = True
        return action

    _write_merged_entry(survivor, merged_meta, merged_body, tags)
    _remove_group_entries(store, removed_entries)
    return action


def cmd_dedupe(args) -> None:
    """Merge duplicate bookmarks based on normalized URLs."""
    store = Path(args.store or DEFAULT_STORE)
    if not store.exists():
        die(f"store not found: {store}")

    buckets = _group_entries_by_url(store)
    dry_run = bool(getattr(args, "dry_run", False))
    actions = []

    for canonical, group in buckets.items():
        if len(group) < 2:
            continue
        actions.append(_process_duplicate_group(store, canonical, group, dry_run))

    if args.json:
        print(json.dumps(actions, ensure_ascii=False))
        return

    prefix = "DRY-RUN: " if dry_run else ""
    if not actions:
        print(f"{prefix}No duplicates found.")
        return

    for action in actions:
        removed = ", ".join(action["removed"])
        notes = "; notes merged" if action["notes_appended"] else ""
        print(
            f"{prefix}{action['total']} duplicates for {action['canonical_url']} -> "
            f"keep {action['kept']}, remove [{removed}]{notes}"
        )

    plural = "s" if len(actions) != 1 else ""
    print(f"{prefix}{len(actions)} duplicate group{plural} processed.")


def cmd_tag(args) -> None:
    """Add or remove tags."""
    store = Path(args.store or DEFAULT_STORE)
    p = resolve_id_or_path(store, args.id)
    if not p:
        die("not found")
    meta, body = load_entry(p)
    cur = set(meta.get("tags", []))
    if args.action == "add":
        cur.update([t.strip() for t in args.tags if t.strip()])
    else:
        cur.difference_update([t.strip() for t in args.tags if t.strip()])
    meta["tags"] = sorted(cur)
    meta["modified"] = iso_now()
    atomic_write(p, build_text(meta, body))


NETSCAPE_HEADER = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<!-- This is an automatically generated file. -->
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
"""
NETSCAPE_FOOTER = "</DL><p>\n"


def _build_netscape_tree(entries: List[Tuple[str, Dict[str, Any]]]) -> str:
    """Build Netscape HTML with folder hierarchy from entries."""
    # entries: list of (path, meta)
    # path is like "dev/python/fastapi-abc"
    # meta has url, title, etc.

    def build_html(node: Dict[str, Any]) -> str:
        html = ""
        # first bookmarks, then folders
        bookmarks = node.get("__bookmarks__", [])
        for bm in bookmarks:
            html += bm
        for key, value in sorted(node.items()):
            if key == "__bookmarks__":
                continue
            if isinstance(value, dict):
                # folder
                html += f"<DT><H3>{key}</H3>\n<DL><p>\n"
                html += build_html(value)
                html += "</DL><p>\n"
        return html

    root = {}
    for path, meta in entries:
        parts = path.split("/")
        current = root
        for part in parts[:-1]:  # all but last are folders
            if part not in current:
                current[part] = {}
            current = current[part]
        # now current is the dict for the folder containing the bookmark
        if "__bookmarks__" not in current:
            current["__bookmarks__"] = []
        add_date = to_epoch(parse_iso(meta.get("created")) or parse_iso(meta.get("modified"))) or ""
        tags = ",".join(meta.get("tags", []))
        title = (
            (meta.get("title") or meta.get("url") or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        url = (meta.get("url") or "").replace("&", "&amp;").replace('"', "&quot;")
        bookmark_html = f'<DT><A HREF="{url}" ADD_DATE="{add_date}" TAGS="{tags}">{title}</A>\n'
        current["__bookmarks__"].append(bookmark_html)

    return build_html(root)


def cmd_export(args) -> None:
    """Export bookmarks."""
    store = Path(args.store or DEFAULT_STORE)
    if args.fmt == "netscape":
        since_dt = parse_iso(args.since) if args.since else None
        want_host = (args.host or "").lower()
        entries = []
        for _, rel, meta, _ in _iter_entries(store):
            if want_host:
                host = urlparse(meta.get("url", "")).netloc.lower()
                if host.startswith("www."):
                    host = host[4:]
                hq = want_host[4:] if want_host.startswith("www.") else want_host
                if host != hq:
                    continue
            ts = parse_iso(meta.get("created")) or parse_iso(meta.get("modified"))
            if since_dt and (not ts or ts < since_dt):
                continue
            entries.append((str(rel), meta))
        html_body = _build_netscape_tree(entries)
        sys.stdout.write(NETSCAPE_HEADER + html_body + NETSCAPE_FOOTER)
    elif args.fmt == "json":
        rows = []
        for _, rel, meta, _ in _iter_entries(store):
            rows.append(
                {
                    "path": str(rel),
                    "url": meta.get("url", ""),
                    "title": meta.get("title", ""),
                    "tags": meta.get("tags", []),
                    "created": meta.get("created", ""),
                    "modified": meta.get("modified", ""),
                }
            )
        print(json.dumps(rows, ensure_ascii=False))
    else:
        die("unknown export format")


def _parse_netscape_html(text: str) -> List[Tuple[str, Dict[str, Any]]]:
    """Parse Netscape HTML and return list of (path, meta) for bookmarks."""
    # Parse the HTML to extract bookmarks with their folder paths
    entries = []
    folder_stack = []  # list of folder names
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # FOLDER START: <DT><H3 ...>Name</H3>
        m = re.search(r"<DT>\s*<H3\b[^>]*>(.*?)</H3>", line, re.I)
        if m:
            folder_name = html.unescape(m.group(1))
            folder_stack.append(folder_name)
            i += 1
            continue
        # BOOKMARK: <DT><A ... HREF="...">Title</A>
        m = re.search(r"<DT>\s*<A\b[^>]*HREF=\"([^\"]+)\"[^>]*>(.*?)</A>", line, re.I)
        if m:
            url, title_html = m.group(1), m.group(2)
            title = html.unescape(re.sub("<[^>]+>", "", title_html))
            tagm = re.search(r'\bTAGS="([^"]*)"', line, re.I)
            tags = [t.strip() for t in (tagm.group(1) if tagm else "").split(",") if t.strip()]
            path = "/".join(folder_stack) if folder_stack else ""
            meta = {
                "url": url,
                "title": title.strip(),
                "tags": tags,
                "created": iso_now(),  # (optional: parse ADD_DATE below)
            }
            # harvest ADD_DATE -> created
            add_date = re.search(r'\bADD_DATE="(\d+)"', line)
            if add_date:
                try:
                    from datetime import datetime, timezone

                    meta["created"] = datetime.fromtimestamp(
                        int(add_date.group(1)), tz=timezone.utc
                    ).isoformat()
                except Exception:
                    pass
            entries.append((path, meta))
            i += 1
            continue

        # FOLDER END: </DL> or </DL><p>
        if re.match(r"</DL\b", line, re.I):
            if folder_stack:
                folder_stack.pop()
            i += 1
            continue

        i += 1
    return entries


def cmd_import(args) -> None:
    """Import bookmarks."""
    store = Path(args.store or DEFAULT_STORE)
    store.mkdir(parents=True, exist_ok=True)
    if args.fmt == "netscape":
        text = Path(args.file).read_text(encoding="utf-8", errors="replace")
        entries = _parse_netscape_html(text)
        for path, meta in entries:
            slug = create_slug_from_url(meta["url"])
            full_path = f"{path}/{slug}" if path else slug
            fpath = id_to_path(store, full_path)
            if fpath.exists() and not args.force:
                continue
            fpath.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(fpath, build_text(meta, ""))
        print("import ok")
    else:
        die("unknown import format")


def cmd_sync(args) -> None:
    """Sync with git."""
    store = Path(args.store or DEFAULT_STORE)
    if not (store / ".git").exists():
        die("store is not a git repo; run: bm init --git", code=2)
    subprocess.run(["git", "add", "-A"], cwd=store)
    subprocess.run(["git", "commit", "-m", "bm sync", "--allow-empty"], cwd=store)
    # push only if upstream exists
    r = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        cwd=store,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if r.returncode == 0:
        subprocess.run(["git", "push"], cwd=store)


def find_candidates(store: Path, needle: str) -> List[Path]:
    """Exact path or fuzzy by filename stem suffix."""
    needle = normalize_slug(needle)
    needle = _reject_unsafe(needle)
    exact = id_to_path(store, needle)
    if exact.exists():
        return [exact]
    name = Path(needle).name  # compare against last component
    hits = []
    for p in store.rglob(f"*{FILE_EXT}"):
        if name in p.stem:
            hits.append(p)
    return sorted(hits)


def resolve_id_or_path(store: Path, token: str) -> Optional[Path]:
    """Accept either a stable ID (by URL) or a path-ish token."""
    token = token.strip()
    # Try ID match (single scan)
    for p in store.rglob(f"*{FILE_EXT}"):
        meta, _ = load_entry(p)
        url = meta.get("url", "")
        if url and rid(url) == token:
            return p
    # Fallback to path/fuzzy
    hits = find_candidates(store, token)
    return hits[0] if hits else None
