"""Command implementations for the bookmark manager."""

import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import webbrowser
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple, Union
from urllib.parse import urlparse

from .dedupe import _merge_entry_group, _select_survivor
from .errors import UnsafePathError
from .io import build_text, load_entry, parse_front_matter
from .models import FILE_EXT, default_store
from .netscape import NETSCAPE_FOOTER, NETSCAPE_HEADER, build_netscape_tree, parse_netscape_html
from .query import SEARCH_FIELDS, FilterSpec, collect_rows, passes_filters, search_rows
from .store import Store
from .utils import (
    _launch_editor,
    _reject_absolute_path,
    _reject_unsafe,
    create_slug_from_url,
    die,
    iso_now,
    normalize_slug,
    normalize_url_for_compare,
    parse_iso,
    rid,
)

ALLOWED_URL_SCHEMES = frozenset({"http", "https", "ftp", "ftps", "mailto"})

_PROGRESS_EVERY = 500


def _store_from_args(args) -> Store:
    """Resolve one concrete store from explicit CLI input or live environment."""
    return Store(Path(args.store) if args.store else default_store())


def _require_store(store: Store, message: str) -> None:
    """Require an existing store and let the CLI render domain errors."""
    store.require_exists(message)


def _progress_tick(label: str, count: int) -> None:
    """Emit a TTY-only progress line on stderr every _PROGRESS_EVERY items."""
    if count and count % _PROGRESS_EVERY == 0 and sys.stderr.isatty():
        sys.stderr.write(f"\rbm: {label}: {count}")
        sys.stderr.flush()


def _progress_done(label: str, count: int) -> None:
    if count >= _PROGRESS_EVERY and sys.stderr.isatty():
        sys.stderr.write(f"\rbm: {label}: {count} (done)\n")
        sys.stderr.flush()


def cmd_init(args) -> None:
    """Initialize a new bookmark store.

    Creates the store directory and optionally initializes a git repository.

    Args:
        args: Parsed command line arguments.
    """
    store = _store_from_args(args)
    store.create()
    print(f"Initialized store at: {store.root}")
    if args.git:
        if (store.root / ".git").exists():
            print("Git repo already exists.")
        else:
            _run_git(store.root, "init")
            print("Initialized git repository.")
    readme = store.root / "README.txt"
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
        try:
            os.chmod(readme, 0o600)
        except OSError:
            pass


def cmd_add(args) -> None:
    """Add a new bookmark."""
    store = _store_from_args(args)
    _require_store(store, f"store not found: {store.root}. Run `bm init` first.")
    url = args.url.strip()
    slug = args.id or create_slug_from_url(url)
    if args.path:
        _reject_absolute_path(args.path)
        _reject_absolute_path(slug)
        slug = f"{normalize_slug(args.path)}/{normalize_slug(slug)}"
    slug = _reject_unsafe(slug)
    fpath = store.path_for(slug)
    if fpath.exists() and not args.force:
        die(f"bookmark exists: {slug} (use --force to overwrite)")
    store.ensure_parent(fpath)

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
        fd, tmp_name = tempfile.mkstemp(suffix=".bm", prefix="bm-")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(template)
            _launch_editor(tmp)
            meta2, body2 = parse_front_matter(tmp.read_text(encoding="utf-8", errors="replace"))
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
        # Editor is source of truth; preserve only the original created timestamp.
        original_url = meta["url"]
        meta = {**meta2, "created": meta["created"]}
        body = body2
        if not meta.get("url"):
            die("url cleared in editor")
        if meta["url"] != original_url:
            print(
                f"bm: warning: url changed in editor; entry stored under original slug "
                f"({slug}). Use `bm mv` to relocate.",
                file=sys.stderr,
            )

    store.write(fpath, build_text(meta, body))
    print(rid(meta.get("url", "")))


def cmd_show(args) -> None:
    """Show a bookmark entry."""
    store = _store_from_args(args)
    p = resolve_id_or_path(store, args.id)
    if not p:
        die("not found")
    assert p is not None
    rel = p.relative_to(store.root).with_suffix("")
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
    store = _store_from_args(args)
    p = resolve_id_or_path(store, args.id)
    if not p:
        die("not found")
    meta, _ = load_entry(p)
    url = meta.get("url")
    if not url:
        die("no url in entry")
    scheme = (urlparse(url).scheme or "").lower()
    if scheme not in ALLOWED_URL_SCHEMES and not getattr(args, "allow_scheme", False):
        die(f"refusing to open {scheme!r} URL (use --allow-scheme to override): {url}")
    ok = webbrowser.open(url)
    print(url)
    if not ok:
        print("bm: warning: system did not acknowledge opening browser", file=sys.stderr)


def _coerce_store(value: Union[Store, Path, str]) -> Store:
    """Accept the concrete boundary while retaining private helper compatibility."""
    return value if isinstance(value, Store) else Store(value)


def _iter_entries(
    store: Union[Store, Path, str], meta_only: bool = False, snapshot: bool = False
) -> Generator[Tuple[Path, Path, Dict[str, Any], str], None, None]:
    """Iterate over entries through the store boundary.

    Files that fail to load are skipped with the established stderr warning.
    Snapshot-aware callers should use ``Store.iter_entries`` directly so the
    precondition bytes remain attached to each :class:`StoreEntry`.
    """
    fs_store = _coerce_store(store)

    def warn(relative: Path, exc: Exception) -> None:
        print(f"bm: skipping {relative}: {exc}", file=sys.stderr)

    for entry in fs_store.iter_entries(meta_only=meta_only, snapshot=snapshot, on_error=warn):
        yield entry.path, entry.relative_path, entry.meta, entry.body


def _filter_spec_from_args(args) -> FilterSpec:
    """Validate CLI filter values and build one immutable query specification."""
    values = vars(args)

    def text_value(name: str) -> str:
        value = values.get(name)
        if value is None:
            return ""
        if not isinstance(value, str):
            raise TypeError(f"--{name} must be a string")
        return value

    tag = text_value("tag") or None
    host = text_value("host").lower()
    path = text_value("path").strip("/")
    since_text = text_value("since")
    since = parse_iso(since_text) if since_text else None
    return FilterSpec(tag=tag, host=host, path=path, since=since)


def _collect_rows(store: Store, filters: FilterSpec) -> List[dict]:
    entries = ((rel, meta) for _, rel, meta, _ in _iter_entries(store, meta_only=True))
    return collect_rows(entries, filters)


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
    store = _store_from_args(args)
    _require_store(store, f"store not found: {store.root}")
    rows = _collect_rows(store, _filter_spec_from_args(args))
    _output_rows(rows, args)


def _search_fields_from_args(args) -> Tuple[str, ...]:
    fields_arg = vars(args).get("field")
    if fields_arg is None:
        return SEARCH_FIELDS
    if not isinstance(fields_arg, list) or not fields_arg:
        raise TypeError("--field must be a non-empty list")
    return tuple(fields_arg)


def _search_regex_from_args(args) -> bool:
    use_regex = vars(args).get("regex", False)
    if not isinstance(use_regex, bool):
        raise TypeError("--regex must be a boolean")
    return use_regex


def _search_entries(store: Store, filters: FilterSpec, fields: Tuple[str, ...]):
    """Yield filtered entries while retaining metadata-only body loading."""
    needs_body = "body" in fields
    for p, rel, meta, _ in _iter_entries(store, meta_only=True):
        if not passes_filters(rel, meta, filters):
            continue
        body = ""
        if needs_body:
            try:
                _, body = load_entry(p)
            except (OSError, ValueError, UnicodeError):
                continue
        yield rel, meta, body


def cmd_search(args) -> None:
    """Search bookmarks."""
    store = _store_from_args(args)
    fields = _search_fields_from_args(args)
    use_regex = _search_regex_from_args(args)
    filters = _filter_spec_from_args(args)
    try:
        hits = search_rows(
            _search_entries(store, filters, fields),
            filters,
            args.query,
            fields,
            use_regex,
        )
    except re.error as exc:
        die(f"invalid --regex pattern: {exc}", code=2)

    _output_rows(hits, args)
    if not hits:
        sys.exit(1)


def cmd_edit(args) -> None:
    """Edit a temporary copy, then atomically commit a validated result."""
    store = _store_from_args(args)
    p = resolve_id_or_path(store, args.id)
    if not p:
        die("not found")

    original = store.snapshot(p)
    fd, tmp_name = tempfile.mkstemp(
        dir=p.parent,
        prefix=f".{p.name}.",
        suffix=".edit",
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(original)
        _launch_editor(tmp)
        edited = tmp.read_text(encoding="utf-8", errors="replace")
        meta, body = parse_front_matter(edited)
        if not meta.get("url"):
            die("url cleared in editor")
        meta["modified"] = iso_now()
        store.write(p, build_text(meta, body), expected=original)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def cmd_rm(args) -> None:
    """Remove bookmark."""
    store = _store_from_args(args)
    p = resolve_id_or_path(store, args.id)
    if not p:
        die("not found")
    assert p is not None
    store.delete(p, expected=store.snapshot(p))


def cmd_mv(args) -> None:
    """Move/rename bookmark."""
    store = _store_from_args(args)
    src = resolve_id_or_path(store, args.src)
    if not src:
        die("source not found")
    _reject_absolute_path(args.dst)
    dst_slug = normalize_slug(args.dst)
    dst_slug = _reject_unsafe(dst_slug)
    dst = store.path_for(dst_slug)
    try:
        store.move(src, dst, expected=store.snapshot(src), force=args.force)
    except FileExistsError:
        die("destination exists (use --force)")
    except OSError as exc:
        die(f"move failed: {exc}")
    print(dst.relative_to(store.root).with_suffix(""))


def cmd_tags(args) -> None:
    """List all tags."""
    store = _store_from_args(args)
    folder_tags = set()
    header_tags = set()
    for _, rel, meta, _ in _iter_entries(store, meta_only=True):
        folder_tags.update(rel.parts[:-1])
        header_tags.update(t.strip() for t in meta.get("tags", []) if t.strip())
    all_tags = sorted(folder_tags | header_tags)
    for t in all_tags:
        print(t)


def cmd_dirs(args) -> None:
    """List known directory prefixes."""
    store = _store_from_args(args)
    dirs = set()
    for _, rel, _, _ in _iter_entries(store, meta_only=True):
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


def _group_entries_by_url(store: Union[Store, Path, str]) -> Dict[str, List[Dict[str, Any]]]:
    fs_store = _coerce_store(store)
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    n = 0

    def warn(relative: Path, exc: Exception) -> None:
        print(f"bm: skipping {relative}: {exc}", file=sys.stderr)

    for entry in fs_store.iter_entries(snapshot=True, on_error=warn):
        url = entry.meta.get("url", "").strip()
        key = normalize_url_for_compare(url)
        if not key:
            continue
        buckets.setdefault(key, []).append(
            {
                "path": entry.path,
                "rel": entry.relative_path,
                "meta": dict(entry.meta),
                "body": entry.body,
                "snapshot": entry.snapshot,
            }
        )
        n += 1
        _progress_tick("dedupe scanning", n)
    _progress_done("dedupe scanning", n)
    return buckets


def _write_merged_entry(
    store: Store,
    survivor: Dict[str, Any],
    merged_meta: Dict[str, Any],
    merged_body: str,
    tags: List[str],
) -> None:
    merged_meta_for_write = dict(merged_meta)
    merged_meta_for_write["tags"] = tags
    if not merged_meta_for_write.get("modified"):
        merged_meta_for_write["modified"] = iso_now()
    data = build_text(merged_meta_for_write, merged_body)
    store.write(survivor["path"], data, expected=survivor["snapshot"])
    survivor["meta"] = merged_meta_for_write
    survivor["body"] = merged_body
    survivor["snapshot"] = data.encode("utf-8")


def _remove_group_entries(store: Store, entries: List[Dict[str, Any]]) -> None:
    for entry in entries:
        store.delete(entry["path"], expected=entry["snapshot"])


def _process_duplicate_group(
    store: Store, canonical: str, group: List[Dict[str, Any]], dry_run: bool
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

    # Validate the complete scan snapshot before changing any member. Each
    # subsequent mutation repeats its own precondition check, so a redundant
    # entry changed after this pass is reported rather than silently deleted.
    for entry in group:
        store.verify(entry["path"], entry["snapshot"])
    _write_merged_entry(store, survivor, merged_meta, merged_body, tags)
    _remove_group_entries(store, removed_entries)
    return action


def cmd_dedupe(args) -> None:
    """Merge duplicate bookmarks based on normalized URLs."""
    store = _store_from_args(args)
    _require_store(store, f"store not found: {store.root}")

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
    store = _store_from_args(args)
    p = resolve_id_or_path(store, args.id)
    if not p:
        die("not found")
    original = store.snapshot(p)
    meta, body = load_entry(p)
    cur = set(meta.get("tags", []))
    if args.action == "add":
        cur.update([t.strip() for t in args.tags if t.strip()])
    else:
        cur.difference_update([t.strip() for t in args.tags if t.strip()])
    meta["tags"] = sorted(cur)
    meta["modified"] = iso_now()
    store.write(p, build_text(meta, body), expected=original)


def _export_row(rel, meta) -> Dict[str, Any]:
    return {
        "path": str(rel),
        "url": meta.get("url", ""),
        "title": meta.get("title", ""),
        "tags": meta.get("tags", []),
        "created": meta.get("created", ""),
        "modified": meta.get("modified", ""),
    }


def _export_netscape(store: Store, filters: FilterSpec) -> None:
    entries = []
    for _, rel, meta, _ in _iter_entries(store, meta_only=True):
        if not passes_filters(rel, meta, filters):
            continue
        entries.append((str(rel), meta))
    html_body = build_netscape_tree(entries)
    sys.stdout.write(NETSCAPE_HEADER + html_body + NETSCAPE_FOOTER)


def _export_json(store: Store, filters: FilterSpec, jsonl: bool) -> None:
    if jsonl:
        # Stream NDJSON one row at a time; output is unsorted.
        for _, rel, meta, _ in _iter_entries(store, meta_only=True):
            if not passes_filters(rel, meta, filters):
                continue
            sys.stdout.write(json.dumps(_export_row(rel, meta), ensure_ascii=False) + "\n")
        return
    rows = []
    for _, rel, meta, _ in _iter_entries(store, meta_only=True):
        if not passes_filters(rel, meta, filters):
            continue
        rows.append(_export_row(rel, meta))
    rows.sort(key=lambda r: r["path"])
    print(json.dumps(rows, ensure_ascii=False))


def cmd_export(args) -> None:
    """Export bookmarks."""
    store = _store_from_args(args)
    filters = _filter_spec_from_args(args)
    if args.fmt == "netscape":
        _export_netscape(store, filters)
    elif args.fmt == "json":
        jsonl = vars(args).get("jsonl", False)
        if not isinstance(jsonl, bool):
            raise TypeError("--jsonl must be a boolean")
        _export_json(store, filters, jsonl)
    else:
        die("unknown export format")


def cmd_import(args) -> None:
    """Import bookmarks from Netscape HTML."""
    store = _store_from_args(args)
    store.create()
    text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    entries = parse_netscape_html(text, default_created=iso_now())
    skipped_scheme = 0
    skipped_unsafe = 0
    written = 0
    for path, meta in entries:
        scheme = (urlparse(meta.get("url", "")).scheme or "").lower()
        if scheme not in ALLOWED_URL_SCHEMES:
            skipped_scheme += 1
            continue
        slug = create_slug_from_url(meta["url"])
        full_path = f"{path}/{slug}" if path else slug
        try:
            fpath = store.path_for(full_path)
        except UnsafePathError:
            skipped_unsafe += 1
            continue
        if fpath.exists() and not args.force:
            continue
        store.write(fpath, build_text(meta, ""))
        written += 1
        _progress_tick("import", written)
    _progress_done("import", written)
    if skipped_scheme:
        print(
            f"bm: skipped {skipped_scheme} entries with disallowed URL scheme",
            file=sys.stderr,
        )
    if skipped_unsafe:
        print(
            f"bm: skipped {skipped_unsafe} entries with unsafe folder paths",
            file=sys.stderr,
        )
    print("import ok")


# Neutralize store-local `.git/config` keys that can run arbitrary commands
# (CVE-2022-39253 family: core.fsmonitor / core.sshCommand / core.hooksPath).
# `-c` overrides win after the per-repo config is read.
_GIT_HARDENED_PREFIX = (
    "git",
    "-c",
    "core.fsmonitor=",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.sshCommand=",
    "-c",
    "credential.helper=",
    "-c",
    "protocol.file.allow=user",
)
_GIT_TIMEOUT_SECONDS = 30


def _git_cmd(*args: str) -> List[str]:
    return [*_GIT_HARDENED_PREFIX, *args]


def _run_git(
    store: Path,
    *args: str,
    check: bool = True,
    capture_output: bool = False,
) -> subprocess.CompletedProcess:
    """Run a hardened, bounded, non-interactive Git command.

    All Git subprocesses go through this seam so repository-local configuration
    cannot re-enable command execution and network operations cannot wait
    forever for credentials or a remote response.
    """
    command = _git_cmd(*args)
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        result = subprocess.run(
            command,
            cwd=store,
            check=check,
            capture_output=capture_output,
            text=capture_output,
            timeout=_GIT_TIMEOUT_SECONDS,
            env=env,
        )
    except subprocess.TimeoutExpired:
        die(
            f"git command timed out after {_GIT_TIMEOUT_SECONDS}s: {' '.join(command)}",
            code=124,
        )
    except subprocess.CalledProcessError as exc:
        die(
            f"git command failed ({' '.join(command)}): exit {exc.returncode}",
            code=exc.returncode or 1,
        )
    except OSError as exc:
        die(f"git command failed ({' '.join(command)}): {exc}", code=2)

    # ``check=True`` guarantees this for the real subprocess implementation;
    # retaining the explicit check keeps the seam deterministic for callers
    # that provide a subprocess test double.
    if check and result.returncode:
        die(
            f"git command failed ({' '.join(command)}): exit {result.returncode}",
            code=result.returncode or 1,
        )
    return result


def cmd_sync(args) -> None:
    """Sync with git."""
    store = _store_from_args(args)
    if not (store.root / ".git").exists():
        die("store is not a git repo; run: bm init --git", code=2)

    _run_git(store.root, "add", "-A")
    _run_git(store.root, "commit", "-m", "bm sync", "--allow-empty")
    # A nonzero rev-parse means no upstream is configured; it is expected and
    # should not be reported as a sync failure.
    upstream = _run_git(
        store.root,
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{u}",
        check=False,
        capture_output=True,
    )
    if upstream.returncode == 0:
        _run_git(store.root, "push")


def resolve_id_or_path(store: Union[Store, Path, str], token: str) -> Optional[Path]:
    """Compatibility wrapper for the concrete store resolver."""
    return _coerce_store(store).resolve(token)
