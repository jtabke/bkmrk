"""Command implementations for the bookmark manager."""

import os
import sys
import json
import shutil
import subprocess
import textwrap
import webbrowser
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from typing import List, Dict, Any, Tuple, Optional, Generator

from .models import DEFAULT_STORE, FILE_EXT
from .utils import (
    die,
    iso_now,
    parse_iso,
    to_epoch,
    normalize_slug,
    _reject_unsafe,
    is_relative_to,
    id_to_path,
    create_slug_from_url,
    rid,
    _launch_editor,
)
from .io import load_entry, atomic_write, build_text, parse_front_matter


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


def cmd_list(args) -> None:
    """List bookmarks."""
    store = Path(args.store or DEFAULT_STORE)
    if not store.exists():
        die(f"store not found: {store}")
    rows = []
    since_dt = parse_iso(args.since) if args.since else None
    want_host = (args.host or "").lower()
    for _, rel, meta, _ in _iter_entries(store):
        # tag filter (folder or header tag)
        if args.tag:
            segs = set(rel.parts[:-1])
            header_tags = set(meta.get("tags", []))
            if args.tag not in segs and args.tag not in header_tags:
                continue
        # host filter (case-insensitive)
        if want_host:
            host = urlparse(meta.get("url", "")).netloc.lower()
            if host.startswith("www."):
                host = host[4:]
            hq = want_host[4:] if want_host.startswith("www.") else want_host
            if host != hq:
                continue
        # since filter (created or modified)
        ts = parse_iso(meta.get("created")) or parse_iso(meta.get("modified"))
        if since_dt and (not ts or ts < since_dt):
            continue
        url = meta.get("url", "")
        rows.append(
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
    # newest first
    rows.sort(key=lambda r: r["_sort"], reverse=True)
    for r in rows:
        r.pop("_sort", None)

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


def cmd_search(args) -> None:
    """Search bookmarks."""
    store = Path(args.store or DEFAULT_STORE)
    q = args.query.lower()
    hits = []
    for _, rel, meta, body in _iter_entries(store):
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


def cmd_export(args) -> None:
    """Export bookmarks."""
    store = Path(args.store or DEFAULT_STORE)
    if args.fmt == "netscape":
        out = [NETSCAPE_HEADER]
        since_dt = parse_iso(args.since) if args.since else None
        want_host = (args.host or "").lower()
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
            add_date = to_epoch(ts) or ""
            tags = ",".join(meta.get("tags", []))
            title = (
                (meta.get("title") or meta.get("url") or "")
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            url = (meta.get("url") or "").replace("&", "&amp;").replace('"', "&quot;")
            out.append(f'<DT><A HREF="{url}" ADD_DATE="{add_date}" TAGS="{tags}">{title}</A>\n')
        out.append(NETSCAPE_FOOTER)
        sys.stdout.write("".join(out))
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


def cmd_import(args) -> None:
    """Import bookmarks."""
    store = Path(args.store or DEFAULT_STORE)
    store.mkdir(parents=True, exist_ok=True)
    if args.fmt == "netscape":
        text = Path(args.file).read_text(encoding="utf-8", errors="replace")
        # crude regex parse for <A ... HREF="...">title</A>
        for m in re.finditer(r'<A\s+[^>]*HREF="([^"]+)"[^>]*>(.*?)</A>', text, flags=re.I | re.S):
            url, title_html = m.group(1), m.group(2)
            title = re.sub("<[^>]+>", "", title_html)
            tagm = re.search(r'TAGS="([^"]+)"', m.group(0))
            tags = [t.strip() for t in tagm.group(1).split(",")] if tagm else []
            slug = create_slug_from_url(url)
            fpath = id_to_path(store, slug)
            if fpath.exists() and not args.force:
                continue
            meta = {
                "url": url,
                "title": title.strip(),
                "tags": [t for t in tags if t],
                "created": iso_now(),
            }
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
        if p.stem.endswith(name):
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

