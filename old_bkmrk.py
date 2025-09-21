#!/usr/bin/env python3
# bm: plain-text, pass-style bookmarks (hardened)
# - stdlib only
# - one .bm file per bookmark (front matter + body)
# - human-readable paths with short-hash to avoid collisions
# - STABLE IDs = hash(URL)  (rename-safe)
# - tags are lists; render quotes when needed; backward-compat read for comma strings
# - JSON/JSONL & Netscape import/export
# - host/since filters with real datetime parsing
# - tag add/rm, git sync with upstream detection
# - atomic writes; path safety checks; better $EDITOR handling

import argparse
import os
import sys
import textwrap
import shutil
import subprocess
import webbrowser
import json
import re
import hashlib
import shlex
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_STORE = Path(os.environ.get("BOOKMARKS_DIR", str(Path.home() / ".bookmarks.d")))
FILE_EXT = ".bm"

FM_START = "---\n"
FM_END = "---\n"

# ---------------------------
# Errors & time helpers
# ---------------------------


def die(msg, code=1):
    print(f"bm: {msg}", file=sys.stderr)
    sys.exit(code)


def iso_now():
    # ISO-8601 with local offset, no microseconds
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _normalize_iso_z(ts: str) -> str:
    # Accept trailing Z and convert to +00:00 for fromisoformat
    return ts[:-1] + "+00:00" if ts and ts.endswith("Z") else ts


def parse_iso(ts: str):
    """Parse ISO-like timestamp. Accepts 'YYYY-MM-DD' and full ISO; returns aware datetime or None."""
    if not ts:
        return None
    ts = ts.strip()
    try:
        # bare date → treat as start-of-day local time
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", ts):
            dt = datetime.fromisoformat(ts + "T00:00:00")
            return dt.astimezone()  # localize
        return datetime.fromisoformat(_normalize_iso_z(ts))
    except Exception:
        return None


def to_epoch(dt: datetime):
    if not dt:
        return None
    return int(dt.timestamp())


# ---------------------------
# Paths & IDs
# ---------------------------


def normalize_slug(s: str) -> str:
    s = s.strip().strip("/").replace(" ", "-")
    s = re.sub(r"[^\w\-/\.]", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "untitled"


def _reject_unsafe(rel: str) -> str:
    parts = [p for p in rel.split("/") if p]
    if any(p == ".." for p in parts):
        die("unsafe path segment '..' not allowed")
    if rel.startswith("/"):
        die("absolute paths not allowed")
    return "/".join(parts)


def is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except Exception:
        return False


def id_to_path(store: Path, slug: str) -> Path:
    slug = normalize_slug(slug)
    slug = _reject_unsafe(slug)
    return store / (slug + FILE_EXT)


def _short_sha(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:7]


def create_slug_from_url(url: str) -> str:
    """Derive human-readable slug + short hash (collision-resistant)."""
    try:
        p = urlparse(url)
        host = (p.netloc or "link").lower().replace("www.", "")
        host = host.replace(":", "-").replace(".", "-")
        last = p.path.strip("/").split("/")[-1] if p.path and p.path != "/" else ""
        base = f"{host}/{last}" if last else host
        base = normalize_slug(base)
    except Exception:
        base = normalize_slug(url.replace("://", "_").replace("/", "-"))
    return f"{base}-{_short_sha(url)}"


def rid(url: str) -> str:
    """Stable short ID based on URL only (rename-safe)."""
    return hashlib.blake2b(url.encode("utf-8"), digest_size=6).hexdigest()


# ---------------------------
# Front matter I/O
# ---------------------------


def _normalize_meta(meta: dict) -> dict:
    """Map legacy keys and ensure shapes."""
    m = dict(meta)
    # legacy -> canonical
    if "added" in m and "created" not in m:
        m["created"] = m.pop("added")
    if "updated" in m and "modified" not in m:
        m["modified"] = m.pop("updated")
    # shapes
    if "tags" in m and isinstance(m["tags"], str):
        m["tags"] = [t.strip() for t in m["tags"].split(",") if t.strip()]
    if "tags" not in m:
        m["tags"] = []
    return m


def parse_front_matter(text: str):
    """
    Simple front matter parser:
    ---\n
    key: value
    ...
    ---\n
    <body>
    Supports:
      - tags: [a, b, "needs,comma"] or "a, b"
      - added/updated (legacy) -> normalized to created/modified
    """
    if not text.startswith(FM_START):
        # No front matter; infer URL from first line as best-effort
        lines = text.splitlines()
        meta = {}
        body = text
        if lines:
            maybe_url = lines[0].strip()
            if maybe_url.startswith("http://") or maybe_url.startswith("https://"):
                meta["url"] = maybe_url
                body = "\n".join(lines[1:]).lstrip("\n")
        return _normalize_meta(meta), body

    rest = text[len(FM_START) :]
    end_idx = rest.find(FM_END)
    if end_idx == -1:
        return _normalize_meta({}), text

    header = rest[:end_idx]
    body = rest[end_idx + len(FM_END) :]
    meta = {}
    for raw in header.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):  # allow comments
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            k = k.strip().lower()
            v = v.strip()
            if k == "tags":
                if v.startswith("[") and v.endswith("]"):
                    inner = v[1:-1].strip()
                    if inner:
                        parts = []
                        buf, inq = "", False
                        for ch in inner:
                            if ch in "\"'":
                                inq = not inq
                                continue
                            if ch == "," and not inq:
                                if buf.strip():
                                    parts.append(buf.strip())
                                buf = ""
                            else:
                                buf += ch
                        if buf.strip():
                            parts.append(buf.strip())
                        meta["tags"] = [t.strip() for t in parts if t.strip()]
                    else:
                        meta["tags"] = []
                else:
                    meta["tags"] = [t.strip() for t in v.split(",") if t.strip()]
            else:
                meta[k] = v
    return _normalize_meta(meta), body.lstrip("\n")


def _fmt_tag(t: str) -> str:
    # Quote tags containing commas, spaces, or empty
    return f'"{t}"' if ("," in t or " " in t or t == "") else t


def build_text(meta: dict, body: str) -> str:
    """Render front matter with ordered keys; lists as [a, b] with quoting when needed."""
    m = _normalize_meta({k: v for k, v in meta.items() if v not in (None, "", [])})
    order = ["url", "title", "tags", "created", "modified", "notes"]
    keys = [k for k in order if k in m] + [k for k in m if k not in order]
    lines = [FM_START]
    for k in keys:
        v = m[k]
        if isinstance(v, list):
            lines.append(f"{k}: [{', '.join(_fmt_tag(t) for t in v)}]\n")
        else:
            if "\n" in str(v):
                lines.append(f"{k}: |\n")
                for ln in str(v).splitlines():
                    lines.append(f"  {ln}\n")
            else:
                lines.append(f"{k}: {v}\n")
    lines.append(FM_END)
    fm = "".join(lines)
    return fm + (body or "")


def load_entry(fpath: Path):
    text = fpath.read_text(encoding="utf-8", errors="replace")
    meta, body = parse_front_matter(text)
    return meta, body


def atomic_write(path: Path, data: str):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------
# Resolution helpers
# ---------------------------


def find_candidates(store: Path, needle: str):
    """Exact path or fuzzy by filename stem suffix."""
    needle = normalize_slug(needle)
    needle = _reject_unsafe(needle)
    exact = id_to_path(store, needle)
    if exact.exists():
        return [exact]
    hits = []
    for p in store.rglob(f"*{FILE_EXT}"):
        if p.stem.endswith(Path(needle).name):
            hits.append(p)
    return hits


def resolve_id_or_path(store: Path, token: str):
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


# ---------------------------
# Commands
# ---------------------------


def cmd_init(args):
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


def cmd_add(args):
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
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
        if editor:
            cmd = shlex.split(editor) + [str(tmp)]
        else:
            cmd = ["notepad", str(tmp)] if os.name == "nt" else ["vi", str(tmp)]
        subprocess.call(cmd, shell=False)
        meta2, body2 = parse_front_matter(
            tmp.read_text(encoding="utf-8", errors="replace")
        )
        try:
            tmp.unlink()
        except Exception:
            pass
        # Merge back (keep created)
        meta.update({k: v for k, v in meta2.items() if k != "created"})
        body = body2

    atomic_write(fpath, build_text(meta, body))
    print(rid(meta.get("url", "")))


def cmd_show(args):
    store = Path(args.store or DEFAULT_STORE)
    p = resolve_id_or_path(store, args.id)
    if not p:
        die("not found")
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


def cmd_open(args):
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
        print(
            "bm: warning: system did not acknowledge opening browser", file=sys.stderr
        )


def _iter_entries(store: Path):
    for p in sorted(store.rglob(f"*{FILE_EXT}")):
        rel = p.relative_to(store).with_suffix("")
        meta, body = load_entry(p)
        yield p, rel, meta, body


def cmd_list(args):
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


def cmd_search(args):
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


def _launch_editor(path: Path):
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if editor:
        cmd = shlex.split(editor) + [str(path)]
    else:
        cmd = ["notepad", str(path)] if os.name == "nt" else ["vi", str(path)]
    subprocess.call(cmd, shell=False)


def cmd_edit(args):
    store = Path(args.store or DEFAULT_STORE)
    p = resolve_id_or_path(store, args.id)
    if not p:
        die("not found")
    _launch_editor(p)
    # bump modified timestamp
    meta, body = load_entry(p)
    meta["modified"] = iso_now()
    atomic_write(p, build_text(meta, body))


def cmd_rm(args):
    store = Path(args.store or DEFAULT_STORE)
    p = resolve_id_or_path(store, args.id)
    if not p:
        die("not found")
    p.unlink()
    # prune empty dirs
    d = p.parent
    while d != store and not any(d.iterdir()):
        d.rmdir()
        d = d.parent


def cmd_mv(args):
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


def cmd_tags(args):
    store = Path(args.store or DEFAULT_STORE)
    folder_tags = set()
    header_tags = set()
    for _, rel, meta, _ in _iter_entries(store):
        folder_tags.update(rel.parts[:-1])
        header_tags.update(t.strip() for t in meta.get("tags", []) if t.strip())
    all_tags = sorted(folder_tags | header_tags)
    for t in all_tags:
        print(t)


def cmd_tag(args):
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


def cmd_export(args):
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
            out.append(
                f'<DT><A HREF="{url}" ADD_DATE="{add_date}" TAGS="{tags}">{title}</A>\n'
            )
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


def cmd_import(args):
    store = Path(args.store or DEFAULT_STORE)
    store.mkdir(parents=True, exist_ok=True)
    if args.fmt == "netscape":
        text = Path(args.file).read_text(encoding="utf-8", errors="replace")
        # crude regex parse for <A ... HREF="...">title</A>
        for m in re.finditer(
            r'<A\s+[^>]*HREF="([^"]+)"[^>]*>(.*?)</A>', text, flags=re.I | re.S
        ):
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


def cmd_sync(args):
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


# ---------------------------
# CLI
# ---------------------------


def main():
    ap = argparse.ArgumentParser(
        prog="bm", description="Plain-text, pass-style bookmarks"
    )
    ap.add_argument(
        "--store",
        help="Path to bookmark store (default: $BOOKMARKS_DIR or ~/.bookmarks.d)",
    )
    sp = ap.add_subparsers(dest="cmd", required=True)

    p = sp.add_parser("init", help="Create a new store")
    p.add_argument(
        "--git", action="store_true", help="Initialize a git repo in the store"
    )
    p.set_defaults(func=cmd_init)

    p = sp.add_parser("add", help="Add a bookmark")
    p.add_argument("url")
    p.add_argument("-n", "--name", help="Title")
    p.add_argument("-t", "--tags", help="Comma-separated tags")
    p.add_argument("-d", "--description", help="Notes / description")
    p.add_argument("-p", "--path", help="Folder path like dev/python")
    p.add_argument("--id", help="Explicit id/slug (relative path ok)")
    p.add_argument(
        "--edit", action="store_true", help="Open $EDITOR with a prefilled template"
    )
    p.add_argument("-f", "--force", action="store_true", help="Overwrite if exists")
    p.set_defaults(func=cmd_add)

    p = sp.add_parser("show", help="Show an entry")
    p.add_argument("id", help="Stable ID or path/slug")
    p.set_defaults(func=cmd_show)

    p = sp.add_parser("open", help="Open in browser")
    p.add_argument("id", help="Stable ID or path/slug")
    p.set_defaults(func=cmd_open)

    p = sp.add_parser("list", help="List all entries")
    p.add_argument("-t", "--tag", help="Filter by tag (folder segment or header tag)")
    p.add_argument("--host", help="Filter by URL host (exact, 'www.' ignored)")
    p.add_argument("--since", help="ISO date/time or YYYY-MM-DD (lower bound)")
    p.add_argument("--json", action="store_true", help="Emit JSON array")
    p.add_argument("--jsonl", action="store_true", help="Emit JSON Lines (NDJSON)")
    p.set_defaults(func=cmd_list)

    p = sp.add_parser("search", help="Full-text search over title/url/tags/body")
    p.add_argument("query")
    p.add_argument("--json", action="store_true", help="Emit JSON array")
    p.add_argument("--jsonl", action="store_true", help="Emit JSON Lines (NDJSON)")
    p.set_defaults(func=cmd_search)

    p = sp.add_parser("edit", help="Edit with $EDITOR / $VISUAL")
    p.add_argument("id")
    p.set_defaults(func=cmd_edit)

    p = sp.add_parser("rm", help="Remove an entry")
    p.add_argument("id")
    p.set_defaults(func=cmd_rm)

    p = sp.add_parser("mv", help="Rename/move an entry")
    p.add_argument("src")
    p.add_argument("dst")
    p.add_argument("-f", "--force", action="store_true")
    p.set_defaults(func=cmd_mv)

    p = sp.add_parser("tags", help="List all discovered tags")
    p.set_defaults(func=cmd_tags)

    p = sp.add_parser("tag", help="Mutate tags without editing")
    p.add_argument("action", choices=["add", "rm"])
    p.add_argument("id")
    p.add_argument("tags", nargs="+")
    p.set_defaults(func=cmd_tag)

    p = sp.add_parser("export", help="Export bookmarks")
    spx = p.add_subparsers(dest="fmt", required=True)
    pe = spx.add_parser("netscape", help="Export as Netscape bookmarks HTML")
    pe.add_argument("--host", help="Filter by URL host")
    pe.add_argument("--since", help="ISO date/time or YYYY-MM-DD lower bound")
    pe.set_defaults(func=cmd_export)
    pj = spx.add_parser("json", help="Export as JSON array")
    pj.set_defaults(func=cmd_export)

    p = sp.add_parser("import", help="Import bookmarks")
    spm = p.add_subparsers(dest="fmt", required=True)
    pn = spm.add_parser("netscape", help="Import from Netscape bookmarks HTML")
    pn.add_argument("file")
    pn.add_argument("-f", "--force", action="store_true", help="Overwrite if exists")
    pn.set_defaults(func=cmd_import)

    p = sp.add_parser("sync", help="git add/commit/push if repo")
    p.set_defaults(func=cmd_sync)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
