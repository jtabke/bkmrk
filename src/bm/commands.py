"""Command implementations for the bookmark manager."""

import io
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import webbrowser
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

from .backup import apply_restore, build_backup, plan_restore, read_backup
from .dedupe import _merge_entry_group, _select_survivor
from .doctor import DoctorReport, inspect_store
from .errors import BmError, NotFoundError, UnsafePathError
from .io import atomic_create, build_text, load_entry, parse_front_matter
from .models import FILE_EXT, default_store
from .netscape import NETSCAPE_FOOTER, NETSCAPE_HEADER, build_netscape_tree, parse_netscape_document
from .query import SEARCH_FIELDS, FilterSpec, collect_rows, passes_filters, search_rows
from .store import Store
from .utils import (
    _launch_editor,
    _reject_unsafe,
    create_slug_from_url,
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


def _warn_skipped(relative: Path, exc: Exception) -> None:
    """Report an unreadable bookmark while continuing a best-effort scan."""
    print(f"bm: skipping {relative}: {exc}", file=sys.stderr)


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


def _add_url(value: str) -> str:
    """Read stdin only when explicitly requested, accepting one URL rather than a batch."""
    if value.strip() != "-":
        return value.strip()
    url = sys.stdin.read().strip()
    if not url or len(url.splitlines()) != 1 or any(char.isspace() for char in url):
        raise BmError("stdin must contain exactly one non-empty URL, without whitespace")
    return url


def cmd_add(args) -> None:
    """Add a new bookmark."""
    store = _store_from_args(args)
    store.require_exists(f"store not found: {store.root}. Run `bm init` first.")
    url = _add_url(args.url)
    slug = args.id or create_slug_from_url(url)
    if args.path:
        folder = normalize_slug(_reject_unsafe(args.path))
        slug = f"{folder}/{normalize_slug(_reject_unsafe(slug))}"
    slug = _reject_unsafe(slug)
    fpath = store.path_for(slug)
    if fpath.exists() and not args.force:
        raise BmError(f"bookmark exists: {slug} (use --force to overwrite)")

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
            raise BmError("url cleared in editor")
        if meta["url"] != original_url:
            print(
                f"bm: warning: url changed in editor; entry stored under original slug "
                f"({slug}). Use `bm mv` to relocate.",
                file=sys.stderr,
            )

    try:
        if args.force:
            store.write(fpath, build_text(meta, body))
        else:
            store.create_entry(fpath, build_text(meta, body))
    except FileExistsError as exc:
        raise BmError(f"bookmark exists: {slug} (use --force to overwrite)") from exc
    print(rid(meta.get("url", "")))


def cmd_show(args) -> None:
    """Show a bookmark entry."""
    store = _store_from_args(args)
    p = store.resolve(args.id)
    if not p:
        raise NotFoundError(
            f"bookmark not found: {args.id!r}; use 'bm search <terms>' or 'bm list' "
            "to find an ID or path"
        )
    rel = p.relative_to(store.root).with_suffix("")
    print(f"# {rel.as_posix()}")
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
    p = store.resolve(args.id)
    if not p:
        raise NotFoundError(
            f"bookmark not found: {args.id!r}; use 'bm search <terms>' or 'bm list' "
            "to find an ID or path"
        )
    meta, _ = load_entry(p)
    _open_url(meta.get("url", ""), allow_scheme=getattr(args, "allow_scheme", False))


def _open_url(url: str, *, allow_scheme: bool = False) -> None:
    """Share the safe browser-launch boundary with interactive selection."""
    if not url:
        raise BmError("no url in entry")
    scheme = (urlparse(url).scheme or "").lower()
    if scheme not in ALLOWED_URL_SCHEMES and not allow_scheme:
        raise BmError(f"refusing to open {scheme!r} URL (use --allow-scheme to override): {url}")
    ok = webbrowser.open(url)
    print(url)
    if not ok:
        print("bm: warning: system did not acknowledge opening browser", file=sys.stderr)


def _filter_spec_from_args(args) -> FilterSpec:
    """Validate CLI filter values and build one immutable query specification."""
    values = vars(args)

    def text_value(name: str) -> str:
        value = values.get(name)
        if value is None:
            return ""
        if not isinstance(value, str):
            raise BmError(f"--{name} must be a string", exit_code=2)
        return value

    tag = text_value("tag") or None
    host = text_value("host").lower()
    path = text_value("path").replace("\\", "/").strip("/")
    since_text = text_value("since")
    since = parse_iso(since_text) if since_text else None
    if since_text and since is None:
        raise BmError(f"invalid --since value: {since_text!r}", exit_code=2)
    return FilterSpec(tag=tag, host=host, path=path, since=since)


def _collect_rows(store: Store, filters: FilterSpec) -> List[dict]:
    entries = (
        (entry.relative_path, entry.meta)
        for entry in store.iter_entries(meta_only=True, on_error=_warn_skipped)
    )
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
    store.require_exists(f"store not found: {store.root}")
    rows = _collect_rows(store, _filter_spec_from_args(args))
    _output_rows(rows, args)


def _search_fields_from_args(args) -> Tuple[str, ...]:
    fields_arg = vars(args).get("field")
    if fields_arg is None:
        return SEARCH_FIELDS
    if not isinstance(fields_arg, list) or not fields_arg:
        raise BmError("--field must be a non-empty list", exit_code=2)
    return tuple(fields_arg)


def _search_regex_from_args(args) -> bool:
    use_regex = vars(args).get("regex", False)
    if not isinstance(use_regex, bool):
        raise BmError("--regex must be a boolean", exit_code=2)
    return use_regex


def _search_entries(store: Store, filters: FilterSpec, fields: Tuple[str, ...]):
    """Yield filtered entries while retaining metadata-only body loading."""
    needs_body = "body" in fields
    for entry in store.iter_entries(meta_only=True, on_error=_warn_skipped):
        if not passes_filters(entry.relative_path, entry.meta, filters):
            continue
        body = ""
        if needs_body:
            try:
                _, body = load_entry(entry.path)
            except (OSError, ValueError, UnicodeError):
                continue
        yield entry.relative_path, entry.meta, body


def cmd_search(args) -> None:
    """Search bookmarks."""
    store = _store_from_args(args)
    fields = _search_fields_from_args(args)
    use_regex = _search_regex_from_args(args)
    filters = _filter_spec_from_args(args)
    query = args.query
    if isinstance(query, list):
        if use_regex and len(query) != 1:
            raise BmError(
                "--regex requires one pattern; quote patterns containing spaces", exit_code=2
            )
        query = " ".join(query)
    try:
        hits = search_rows(_search_entries(store, filters, fields), query, fields, use_regex)
    except re.error as exc:
        raise BmError(f"invalid --regex pattern: {exc}", exit_code=2) from exc

    _output_rows(hits, args)
    if not hits:
        raise BmError(exit_code=1)


def _pick_row(rows: List[dict]) -> dict:
    """Exchange only sanitized display rows with fzf, never shell commands."""
    choices = {}
    for index, row in enumerate(rows):
        display = "  ".join(str(row[key]) for key in ("path", "title", "url"))
        display = "".join(
            " " if ord(char) < 32 or 127 <= ord(char) < 160 else char for char in display
        )
        choices[f"{index}\t{display}"] = row
    env = os.environ.copy()
    # Custom fzf bindings/previews can execute shell commands or change its output
    # protocol. Keep this narrowly scoped picker independent of those settings.
    env.pop("FZF_DEFAULT_OPTS", None)
    env.pop("FZF_DEFAULT_OPTS_FILE", None)
    try:
        result = subprocess.run(
            [
                "fzf",
                "--no-multi",
                "--delimiter=\\t",
                "--with-nth=2..",
                "--prompt=bm> ",
                "--no-preview",
            ],
            input="\n".join(choices) + "\n",
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=env,
            check=False,
        )
    except FileNotFoundError as exc:
        raise BmError("fzf is not installed; install fzf to use 'bm pick'", exit_code=2) from exc
    except OSError as exc:
        raise BmError(f"could not start fzf: {exc}", exit_code=2) from exc
    if result.returncode in (1, 130):
        raise BmError(exit_code=result.returncode)
    if result.returncode:
        raise BmError(f"fzf failed with exit status {result.returncode}", exit_code=2)
    selection = result.stdout.rstrip("\r\n")
    if selection not in choices:
        raise BmError("fzf returned an invalid selection; no bookmark opened", exit_code=2)
    return choices[selection]


def cmd_pick(args) -> None:
    """Filter bookmarks and let optional fzf select one without modifying the store."""
    store = _store_from_args(args)
    store.require_exists(f"store not found: {store.root}; check --store or run 'bm init'")
    rows = _collect_rows(store, _filter_spec_from_args(args))
    if not rows:
        raise BmError("no bookmarks match; use 'bm list' or adjust the filters")
    selected = _pick_row(rows)
    if args.print_field:
        print(selected[args.print_field])
    else:
        _open_url(selected["url"])


def cmd_edit(args) -> None:
    """Edit a temporary copy, then atomically commit a validated result."""
    store = _store_from_args(args)
    p = store.resolve(args.id)
    if not p:
        raise NotFoundError(
            f"bookmark not found: {args.id!r}; use 'bm search <terms>' or 'bm list' "
            "to find an ID or path"
        )

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
            raise BmError("url cleared in editor")
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
    p = store.resolve(args.id)
    if not p:
        raise NotFoundError(
            f"bookmark not found: {args.id!r}; use 'bm search <terms>' or 'bm list' "
            "to find an ID or path"
        )
    store.delete(p, expected=store.snapshot(p))


def cmd_mv(args) -> None:
    """Move/rename bookmark."""
    store = _store_from_args(args)
    src = store.resolve(args.src)
    if not src:
        raise NotFoundError(f"source not found: {args.src!r}; use 'bm list' to find an ID or path")
    dst = store.path_for(args.dst)
    try:
        store.move(src, dst, expected=store.snapshot(src), force=args.force)
    except FileExistsError as exc:
        raise BmError(
            f"destination exists: {args.dst!r}; choose a different path or use --force to replace it"
        ) from exc
    except OSError as exc:
        raise BmError(f"move failed: {exc}") from exc
    print(dst.relative_to(store.root).with_suffix("").as_posix())


def cmd_tags(args) -> None:
    """List all tags."""
    store = _store_from_args(args)
    folder_tags = set()
    header_tags = set()
    for entry in store.iter_entries(meta_only=True, on_error=_warn_skipped):
        folder_tags.update(entry.relative_path.parts[:-1])
        header_tags.update(t.strip() for t in entry.meta.get("tags", []) if t.strip())
    all_tags = sorted(folder_tags | header_tags)
    for t in all_tags:
        print(t)


def cmd_dirs(args) -> None:
    """List known directory prefixes."""
    store = _store_from_args(args)
    dirs = set()
    for entry in store.iter_entries(meta_only=True, on_error=_warn_skipped):
        # Add all parent directories
        parts = entry.relative_path.parts
        for i in range(1, len(parts)):
            dirs.add("/".join(parts[:i]))
    all_dirs = sorted(dirs)
    if args.json:
        print(json.dumps(all_dirs, ensure_ascii=False))
    else:
        for d in all_dirs:
            print(d)


def _group_entries_by_url(store: Store) -> Dict[str, List[Dict[str, Any]]]:
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    n = 0

    for entry in store.iter_entries(snapshot=True, on_error=_warn_skipped):
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
        "kept": survivor["rel"].as_posix(),
        "removed": [entry["rel"].as_posix() for entry in removed_entries],
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
    for entry in removed_entries:
        store.delete(entry["path"], expected=entry["snapshot"])
    return action


def cmd_dedupe(args) -> None:
    """Merge duplicate bookmarks based on normalized URLs."""
    store = _store_from_args(args)
    store.require_exists(f"store not found: {store.root}")

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
    p = store.resolve(args.id)
    if not p:
        raise NotFoundError(
            f"bookmark not found: {args.id!r}; use 'bm search <terms>' or 'bm list' "
            "to find an ID or path"
        )
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
        "path": rel.as_posix(),
        "url": meta.get("url", ""),
        "title": meta.get("title", ""),
        "tags": meta.get("tags", []),
        "created": meta.get("created", ""),
        "modified": meta.get("modified", ""),
    }


def _export_netscape(store: Store, filters: FilterSpec) -> None:
    entries = []
    for entry in store.iter_entries(meta_only=True, on_error=_warn_skipped):
        if not passes_filters(entry.relative_path, entry.meta, filters):
            continue
        entries.append((entry.relative_path.as_posix(), entry.meta))
    html_body = build_netscape_tree(entries)
    sys.stdout.write(NETSCAPE_HEADER + html_body + NETSCAPE_FOOTER)


def _export_json(store: Store, filters: FilterSpec, jsonl: bool) -> None:
    if jsonl:
        # Stream NDJSON one row at a time; output is unsorted.
        for entry in store.iter_entries(meta_only=True, on_error=_warn_skipped):
            if not passes_filters(entry.relative_path, entry.meta, filters):
                continue
            sys.stdout.write(
                json.dumps(_export_row(entry.relative_path, entry.meta), ensure_ascii=False) + "\n"
            )
        return
    rows = []
    for entry in store.iter_entries(meta_only=True, on_error=_warn_skipped):
        if not passes_filters(entry.relative_path, entry.meta, filters):
            continue
        rows.append(_export_row(entry.relative_path, entry.meta))
    rows.sort(key=lambda r: r["path"])
    print(json.dumps(rows, ensure_ascii=False))


def cmd_export(args) -> None:
    """Export to stdout or safely publish a completed file in any format."""
    store = _store_from_args(args)
    output = getattr(args, "output", None)
    if output is None:
        _render_export(args, store)
        return
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        _render_export(args, store)
    try:
        atomic_create(Path(output), buffer.getvalue().encode("utf-8"))
    except FileExistsError as exc:
        raise BmError(f"output already exists: {output}; choose a new output filename") from exc


def _render_export(args, store: Store) -> None:
    """Retain streaming stdout behavior when no output file is requested."""
    if args.fmt is None:
        sys.stdout.write(build_backup(store))
        return
    filters = _filter_spec_from_args(args)
    if args.fmt == "netscape":
        _export_netscape(store, filters)
    elif args.fmt == "json":
        jsonl = vars(args).get("jsonl", False)
        if not isinstance(jsonl, bool):
            raise BmError("--jsonl must be a boolean", exit_code=2)
        _export_json(store, filters, jsonl)
    else:
        raise BmError("unknown export format")


def _import_backup(args, store: Store, text: str) -> None:
    """Preflight a complete backup and report conflicts before writing."""
    plan = plan_restore(store, read_backup(text), ALLOWED_URL_SCHEMES, force=args.force)
    dry_run = getattr(args, "dry_run", False)
    if dry_run or plan.conflicts:
        print(plan.summary(preview=True))
    if plan.conflicts:
        details = "\n".join(f"  {name}" for name in plan.conflicts)
        raise BmError(
            f"{len(plan.conflicts)} conflicting paths:\n{details}\n"
            "No files written. Use --force to replace conflicting files."
        )
    if not dry_run:
        try:
            apply_restore(store, plan)
        except (OSError, BmError) as exc:
            raise BmError(
                f"restore stopped: {exc}; earlier files may have been written. "
                "Inspect the store and retry."
            ) from exc
        print(plan.summary(preview=False))


def _check_import_root(store: Store) -> None:
    """Do not preview a store whose root or ancestors cannot be directories."""
    for parent in (store.root, *store.root.parents):
        if (parent.exists() or parent.is_symlink()) and not parent.is_dir():
            raise BmError(
                f"store path is blocked by a non-directory: {parent}; choose another --store path"
            )


def cmd_import(args) -> None:
    """Recognize a lossless JSON backup or import Netscape HTML."""
    store = _store_from_args(args)
    try:
        if args.file == "-":
            stream = getattr(sys.stdin, "buffer", None)
            raw = stream.read() if stream is not None else sys.stdin.read().encode("utf-8")
        else:
            raw = Path(args.file).read_bytes()
    except FileNotFoundError as exc:
        raise NotFoundError(f"import file not found: {args.file}") from exc
    if not raw.strip():
        raise BmError("import input is empty; provide a backup or browser export")
    _check_import_root(store)
    text = raw.decode("utf-8-sig", errors="replace")
    if text.lstrip().startswith(("{", "[")) or Path(args.file).suffix.lower() in {
        ".json",
        ".jsonl",
    }:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeError as exc:
            raise BmError("backup must be valid UTF-8") from exc
        _import_backup(args, store, text)
        return
    _import_netscape(args, store, text)


def _check_import_destination(store: Store, path: Path) -> None:
    """Reject blocked or linked destinations identically in previews and writes."""
    current = store.root
    for part in path.relative_to(store.root).parts:
        current = current / part
        if current.is_symlink():
            raise UnsafePathError(f"symlink in import destination: {current}")
        if not current.exists():
            continue
        if current == path:
            if not current.is_file():
                raise UnsafePathError(f"destination is not a regular file: {current}")
        elif not current.is_dir():
            raise UnsafePathError(f"parent is not a directory: {current}")


def _netscape_import_path(
    store: Store, folder: str, meta: dict, counts: dict[str, int]
) -> Path | None:
    """Validate a browser record and retain diagnostic counts for previews."""
    try:
        parsed = urlparse(meta.get("url", ""))
        scheme = parsed.scheme.lower()
        if scheme not in ALLOWED_URL_SCHEMES:
            counts["invalid_scheme"] += 1
            return None
        if scheme == "mailto":
            if not parsed.path:
                raise ValueError("missing mail address")
        elif not parsed.hostname:
            raise ValueError("missing URL host")
        _ = parsed.port
        slug = create_slug_from_url(meta["url"])
    except ValueError as exc:
        counts["invalid_url"] += 1
        print(f"bm: skipped invalid import URL: {exc}", file=sys.stderr)
        return None
    full_path = f"{folder}/{slug}" if folder else slug
    try:
        destination = store.path_for(full_path)
        _check_import_destination(store, destination)
        return destination
    except UnsafePathError as exc:
        print(f"bm: skipped import path {full_path!r}: {exc}", file=sys.stderr)
        counts["unsafe"] += 1
        return None


def _import_netscape(args, store: Store, text: str) -> None:
    """Import browser data, retaining the existing best-effort behavior."""
    dry_run = getattr(args, "dry_run", False)
    entries, missing_href = parse_netscape_document(text, default_created=iso_now())
    counts = dict(
        added=0,
        replaced=0,
        skipped_existing=0,
        invalid_scheme=0,
        invalid_url=0,
        unsafe=0,
        missing_href=missing_href,
    )
    if not dry_run:
        store.create()
    seen_paths: set[Path] = set()
    for folder, meta in entries:
        fpath = _netscape_import_path(store, folder, meta, counts)
        if fpath is None:
            continue
        exists = fpath.exists() or fpath in seen_paths
        if exists and not args.force:
            counts["skipped_existing"] += 1
            continue
        if not dry_run:
            try:
                if args.force:
                    store.write(fpath, build_text(meta, ""))
                else:
                    store.create_entry(fpath, build_text(meta, ""))
            except FileExistsError:
                counts["skipped_existing"] += 1
                continue
        seen_paths.add(fpath)
        counts["replaced" if exists else "added"] += 1
        if not dry_run:
            _progress_tick("import", counts["added"] + counts["replaced"])
    if not dry_run:
        _progress_done("import", counts["added"] + counts["replaced"])
    _report_netscape_import(counts, dry_run)


def _report_netscape_import(counts: dict[str, int], dry_run: bool) -> None:
    """Keep browser import diagnostics and preview output together."""
    for key, label in (
        ("invalid_scheme", "disallowed URL scheme"),
        ("unsafe", "unsafe folder paths"),
        ("missing_href", "missing URL attributes"),
    ):
        if counts[key]:
            print(f"bm: skipped {counts[key]} entries with {label}", file=sys.stderr)
    if dry_run:
        invalid = sum(
            counts[key] for key in ("invalid_scheme", "invalid_url", "unsafe", "missing_href")
        )
        print(
            f"Would add {counts['added']}, replace {counts['replaced']}, "
            f"skip existing {counts['skipped_existing']}; invalid {invalid}.\nNo files written."
        )
    else:
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
    except subprocess.TimeoutExpired as exc:
        raise BmError(
            f"git command timed out after {_GIT_TIMEOUT_SECONDS}s: {' '.join(command)}",
            exit_code=124,
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise BmError(
            f"git command failed ({' '.join(command)}): exit {exc.returncode}",
            exit_code=exc.returncode or 1,
        ) from exc
    except OSError as exc:
        raise BmError(f"git command failed ({' '.join(command)}): {exc}", exit_code=2) from exc

    # ``check=True`` guarantees this for the real subprocess implementation;
    # retaining the explicit check keeps the seam deterministic for callers
    # that provide a subprocess test double.
    if check and result.returncode:
        raise BmError(
            f"git command failed ({' '.join(command)}): exit {result.returncode}",
            exit_code=result.returncode or 1,
        )
    return result


def _doctor_git(store: Store, report: DoctorReport) -> None:
    """Check local Git discovery, HEAD shape, and locks without status/index writes."""
    marker = store.root / ".git"
    if marker.is_symlink():
        report.add(
            "git_symlink",
            [".git"],
            "Git metadata is a symlink and was not inspected.",
            "Inspect the link manually before running Git commands.",
        )
        return
    if not marker.exists():
        return
    try:
        result = _run_git(store.root, "rev-parse", "--absolute-git-dir", capture_output=True)
        git_dir = Path(result.stdout.strip())
        head = git_dir / "HEAD"
        if head.is_symlink():
            raise BmError("Git HEAD is a symlink")
        text = head.read_text(encoding="utf-8").strip()
        if text.startswith("ref: "):
            _run_git(store.root, "check-ref-format", text[5:], capture_output=True)
        elif not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", text):
            raise BmError("Git HEAD is malformed")
        for name in ("index.lock", "HEAD.lock", "config.lock", "packed-refs.lock", "shallow.lock"):
            path = git_dir / name
            if path.exists() or path.is_symlink():
                report.add(
                    "git_lock",
                    [f".git/{name}"],
                    "Git lock file is present.",
                    "Check for active Git processes before considering manual lock removal.",
                    warning=True,
                )
    except (BmError, OSError, UnicodeError) as exc:
        report.add(
            "git_problem",
            [".git"],
            str(exc),
            "Inspect Git metadata/configuration manually; doctor never changes it.",
        )


def cmd_doctor(args) -> None:
    """Report problems without repair, network access, or browser/editor launches."""
    store = _store_from_args(args)
    report = inspect_store(store, ALLOWED_URL_SCHEMES)
    if store.exists():
        _doctor_git(store, report)
    document = report.document()
    if args.json:
        print(json.dumps(document, ensure_ascii=False))
    elif document["ok"]:
        noun = "bookmark" if report.checked == 1 else "bookmarks"
        print(f"Store healthy: {report.checked} {noun} checked.")
    else:
        for item in document["findings"]:
            paths = ", ".join(repr(path) for path in item["paths"])
            print(f"{item['severity'].upper()} {item['code']}: {paths}")
            print(f"  {item['message']}\n  Next: {item['hint']}")
        print(
            f"Checked bookmarks: {report.checked}; findings: {len(report.findings)}. No files changed."
        )
    if report.findings:
        raise BmError(exit_code=1)


def cmd_sync(args) -> None:
    """Sync with git."""
    store = _store_from_args(args)
    if not (store.root / ".git").exists():
        raise BmError("store is not a git repo; run: bm init --git", exit_code=2)

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
