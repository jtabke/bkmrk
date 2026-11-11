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
from typing import List, TextIO, Tuple

from .backup import apply_restore, plan_restore, read_backup, write_backup
from .clipboard import read_clipboard, write_clipboard
from .dedupe import DedupePlan, apply_dedupe, plan_dedupe
from .doctor import inspect_store
from .editor import launch_editor
from .errors import BmError, NotFoundError
from .exporting import write_json, write_netscape
from .git import inspect_git, run_git
from .importing import ImportCounts, import_netscape
from .io import atomic_text_create, build_text, load_entry, parse_front_matter
from .models import FILE_EXT, default_store
from .paths import normalize_slug, reject_unsafe
from .query import SEARCH_FIELDS, FilterSpec, collect_rows, passes_filters, search_rows
from .store import Store
from .urls import ALLOWED_URL_SCHEMES, inspect_url
from .utils import (
    create_slug_from_url,
    iso_now,
    normalize_url_for_compare,
    parse_iso,
    rid,
)

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
            run_git(store.root, "init")
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


def _single_capture_url(text: str, source: str) -> str:
    """Accept one copied/piped URL rather than a batch or arbitrary prose."""
    url = text.strip()
    if not url or len(url.splitlines()) != 1 or any(char.isspace() for char in url):
        raise BmError(f"{source} must contain exactly one non-empty URL, without whitespace")
    return url


def _add_url(value: str | None, *, clipboard: bool = False) -> str:
    """Read stdin or the clipboard only when explicitly requested."""
    if clipboard:
        url = _single_capture_url(read_clipboard(), "clipboard")
        inspection = inspect_url(url)
        if not inspection.supported or inspection.problem:
            raise BmError("clipboard must contain a valid http, https, ftp, ftps, or mailto URL")
        return url
    if value is None:
        raise BmError("provide a URL, - for stdin, or --clipboard", exit_code=2)
    if value.strip() == "-":
        return _single_capture_url(sys.stdin.read(), "stdin")
    return value.strip()


def _warn_existing_urls(store: Store, url: str, destination: Path) -> None:
    """Report advisory matches after saving without redefining bookmark identity."""
    try:
        key = normalize_url_for_compare(url)
    except ValueError:
        return
    if not key:
        return
    matches = []
    for entry in store.iter_entries(meta_only=True, on_error=_warn_skipped):
        if entry.path == destination:
            continue
        try:
            existing_key = normalize_url_for_compare(entry.meta.get("url", ""))
        except ValueError:
            continue
        if existing_key == key:
            matches.append(entry.relative_path.as_posix())
    for match in sorted(matches):
        print(
            f"bm: warning: equivalent URL already stored at {match!r}; "
            "use 'bm show <path>' or 'bm open <path>' to inspect it. "
            "Saved separately; existing bookmark left unchanged.",
            file=sys.stderr,
        )


def cmd_add(args) -> None:
    """Add a new bookmark."""
    store = _store_from_args(args)
    store.require_exists(f"store not found: {store.root}. Run `bm init` first.")
    url = _add_url(args.url, clipboard=getattr(args, "clipboard", False))
    slug = args.id or create_slug_from_url(url)
    if args.path:
        folder = normalize_slug(reject_unsafe(args.path))
        slug = f"{folder}/{normalize_slug(reject_unsafe(slug))}"
    slug = reject_unsafe(slug)
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
            launch_editor(tmp)
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
    _warn_existing_urls(store, meta.get("url", ""), fpath)
    print(rid(meta.get("url", "")))


def cmd_show(args) -> None:
    """Show a bookmark entry."""
    store = _store_from_args(args)
    p = store.require_entry(args.id)
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
    """Resolve directly or select interactively, then open, copy, or print the URL."""
    values = vars(args)
    direct = args.id is not None
    filtered = any(values.get(name) is not None for name in ("tag", "host", "path", "since"))
    if direct and (values.get("query") is not None or filtered):
        raise BmError(
            "an ID/path cannot be combined with --search or selection filters", exit_code=2
        )
    if values.get("allow_scheme") and (
        not direct or values.get("copy") or values.get("print_field")
    ):
        raise BmError(
            "--allow-scheme requires direct ID/path opening, not selection/copy/print", exit_code=2
        )
    store = _store_from_args(args)
    if direct:
        meta, _ = load_entry(store.require_entry(args.id), meta_only=True)
        url = meta.get("url", "")
    else:
        url = _select_open_row(store, args)["url"]
    if not url:
        raise BmError("no url in entry")
    if values.get("copy"):
        _copy_url(url)
    elif values.get("print_field"):
        print(rid(url) if args.print_field == "id" else url)
    else:
        _open_url(url, allow_scheme=values.get("allow_scheme", False))


def cmd_copy(args) -> None:
    """Copy only the stored URL without launching a browser or changing the store."""
    store = _store_from_args(args)
    meta, _ = load_entry(store.require_entry(args.id), meta_only=True)
    url = meta.get("url", "")
    _copy_url(url)


def _copy_url(url: str) -> None:
    """Share explicit clipboard output between direct lookup and interactive selection."""
    if not url:
        raise BmError("no url in entry")
    write_clipboard(url)
    print("Copied URL to clipboard.", file=sys.stderr)


def _open_url(url: str, *, allow_scheme: bool = False) -> None:
    """Share the safe browser-launch boundary with interactive selection."""
    if not url:
        raise BmError("no url in entry")
    inspection = inspect_url(url)
    if inspection.problem and not inspection.scheme:
        raise BmError(f"invalid URL: {inspection.problem}", exit_code=2)
    scheme = inspection.scheme
    if scheme not in ALLOWED_URL_SCHEMES and not allow_scheme:
        raise BmError(
            f"refusing to open {scheme!r} URL "
            f"(use 'bm open <ID|path> --allow-scheme' to override intentionally): {url}"
        )
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
        for entry in store.iter_entries(
            meta_only=True,
            path_prefix=filters.path,
            on_error=_warn_skipped,
        )
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
    """Read bodies once unless metadata filters can avoid loading them."""
    needs_body = "body" in fields
    # Path filters run before reads; metadata filters can reject large bodies
    # after reading only the header. Otherwise, load each entry just once.
    full_read = needs_body and not (filters.tag or filters.host or filters.since)
    for entry in store.iter_entries(
        meta_only=not full_read,
        path_prefix=filters.path,
        on_error=_warn_skipped,
    ):
        if not passes_filters(entry.relative_path, entry.meta, filters):
            continue
        body = entry.body
        if needs_body and not full_read:
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
        raise BmError(
            "fzf is not installed; install fzf for interactive 'bm open', "
            "or provide an ID/path to open directly",
            exit_code=2,
        ) from exc
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


def _select_open_row(store: Store, args) -> dict:
    """Select one filtered bookmark only when input and UI output are terminals."""
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise BmError(
            "interactive selection requires a terminal on stdin and stderr; "
            "provide an ID/path or use 'bm search <terms> --json' in scripts",
            exit_code=2,
        )
    store.require_exists(f"store not found: {store.root}; check --store or run 'bm init'")
    filters = _filter_spec_from_args(args)
    query = getattr(args, "query", None) or ""
    rows = (
        search_rows(_search_entries(store, filters, SEARCH_FIELDS), query, SEARCH_FIELDS, False)
        if query.strip()
        else _collect_rows(store, filters)
    )
    if not rows:
        raise BmError("no bookmarks match; use 'bm list' or adjust the search terms/filters")
    return _pick_row(rows)


def cmd_edit(args) -> None:
    """Edit a temporary copy, then atomically commit a validated result."""
    store = _store_from_args(args)
    p = store.require_entry(args.id)

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
        launch_editor(tmp)
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
    p = store.require_entry(args.id)
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


def _apply_dedupe_with_guidance(store: Store, plan: DedupePlan) -> None:
    """Preserve failure types/statuses while making possible partial progress explicit."""
    try:
        apply_dedupe(store, plan)
    except (OSError, BmError) as exc:
        message = (
            f"dedupe stopped: {exc}; earlier changes may have been applied. "
            "Inspect the store and rerun 'bm dedupe --dry-run' before retrying."
        )
        if isinstance(exc, BmError):
            raise type(exc)(message, exit_code=exc.exit_code) from exc
        raise BmError(message, exit_code=2) from exc


def cmd_dedupe(args) -> None:
    """Merge duplicate bookmarks based on normalized URLs."""
    store = _store_from_args(args)
    store.require_exists(f"store not found: {store.root}")

    plan = plan_dedupe(
        store,
        on_error=_warn_skipped,
        on_progress=lambda count: _progress_tick("dedupe scanning", count),
    )
    _progress_done("dedupe scanning", plan.scanned)
    dry_run = bool(getattr(args, "dry_run", False))
    if not dry_run:
        _apply_dedupe_with_guidance(store, plan)
    actions = [group.document(dry_run=dry_run) for group in plan.groups]

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
    p = store.require_entry(args.id)
    entry = store.read_entry(p, snapshot=True)
    original = entry.snapshot
    meta, body = dict(entry.meta), entry.body
    cur = set(meta.get("tags", []))
    if args.action == "add":
        cur.update([t.strip() for t in args.tags if t.strip()])
    else:
        cur.difference_update([t.strip() for t in args.tags if t.strip()])
    meta["tags"] = sorted(cur)
    meta["modified"] = iso_now()
    store.write(p, build_text(meta, body), expected=original)


def cmd_export(args) -> None:
    """Export to stdout or safely publish a completed file in any format."""
    store = _store_from_args(args)
    output = getattr(args, "output", None)
    if output is None:
        _render_export(args, store, sys.stdout)
        return
    try:
        with atomic_text_create(Path(output)) as stream:
            _render_export(args, store, stream)
    except FileExistsError as exc:
        raise BmError(f"output already exists: {output}; choose a new output filename") from exc


def _render_export(args, store: Store, stream: TextIO) -> None:
    """Serialize directly into a supplied stream, without redirecting global stdout."""
    if args.fmt is None:
        write_backup(store, stream)
        return
    filters = _filter_spec_from_args(args)
    if args.fmt == "netscape":
        write_netscape(store, filters, stream, on_error=_warn_skipped)
    elif args.fmt == "json":
        jsonl = vars(args).get("jsonl", False)
        if not isinstance(jsonl, bool):
            raise BmError("--jsonl must be a boolean", exit_code=2)
        write_json(store, filters, stream, jsonl=jsonl, on_error=_warn_skipped)
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
    store.check_root()
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
    counts = import_netscape(
        store,
        text,
        force=args.force,
        dry_run=getattr(args, "dry_run", False),
        on_warning=lambda message: print(f"bm: {message}", file=sys.stderr),
        on_progress=lambda count: _progress_tick("import", count),
    )
    if not getattr(args, "dry_run", False):
        _progress_done("import", counts.added + counts.replaced)
    _report_netscape_import(counts, getattr(args, "dry_run", False))


def _report_netscape_import(counts: ImportCounts, dry_run: bool) -> None:
    """Keep browser import diagnostics and preview output together."""
    for key, label in (
        ("invalid_scheme", "disallowed URL scheme"),
        ("unsafe", "unsafe folder paths"),
        ("missing_href", "missing URL attributes"),
    ):
        count = getattr(counts, key)
        if count:
            print(f"bm: skipped {count} entries with {label}", file=sys.stderr)
    if dry_run:
        print(
            f"Would add {counts.added}, replace {counts.replaced}, "
            f"skip existing {counts.skipped_existing}; invalid {counts.invalid}.\nNo files written."
        )
    else:
        print("import ok")


def cmd_doctor(args) -> None:
    """Report problems without repair, network access, or browser/editor launches."""
    store = _store_from_args(args)
    report = inspect_store(store, ALLOWED_URL_SCHEMES)
    if store.exists():
        inspect_git(store, report)
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

    run_git(store.root, "add", "-A")
    run_git(store.root, "commit", "-m", "bm sync", "--allow-empty")
    # A nonzero rev-parse means no upstream is configured; it is expected and
    # should not be reported as a sync failure.
    upstream = run_git(
        store.root,
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{u}",
        check=False,
        capture_output=True,
    )
    if upstream.returncode == 0:
        run_git(store.root, "push")
