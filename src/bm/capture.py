"""Single and batch capture with advisory duplicate warnings and checked publication."""

import sys
from argparse import Namespace
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from contextlib import nullcontext
from pathlib import Path

from .dedupe import duplicate_key
from .errors import BmError, ConflictError
from .io import build_text, parse_front_matter
from .models import Metadata, metadata_tags, metadata_text
from .paths import bookmark_token, normalize_slug, reject_unsafe
from .store import Store, StoreEntry
from .urls import inspect_url
from .utils import create_slug_from_url, iso_now, rid


def _single_capture_url(text: str, source: str) -> str:
    """Accept one copied/piped URL rather than a batch or arbitrary prose."""
    url = text.strip()
    if not url or len(url.splitlines()) != 1 or any(char.isspace() for char in url):
        raise BmError(f"{source} must contain exactly one non-empty URL, without whitespace")
    return url


def _validated_capture_url(text: str, source: str) -> str:
    url = _single_capture_url(text, source)
    inspection = inspect_url(url)
    if not inspection.supported or inspection.problem:
        raise BmError(f"{source} must contain a valid http, https, ftp, ftps, or mailto URL")
    return url


def _add_url(value: str | None, *, clipboard: bool = False) -> str:
    """Read stdin or the clipboard only when explicitly requested."""
    if clipboard:
        from .clipboard import read_clipboard

        return _validated_capture_url(read_clipboard(), "clipboard")
    if value is None:
        raise BmError("provide a URL, - for stdin, or --clipboard", exit_code=2)
    if value.strip() == "-":
        return _single_capture_url(sys.stdin.read(), "stdin")
    return value.strip()


def _capture_key(url: str) -> str:
    try:
        return duplicate_key(url)
    except ValueError:
        return ""


def _stored_url_keys(entries: Iterable[StoreEntry]) -> Iterator[tuple[str, Path]]:
    for entry in entries:
        if key := _capture_key(metadata_text(entry.meta, "url")):
            yield key, entry.path


def _warn_existing_urls(store: Store, destination: Path, paths: Iterable[Path]) -> None:
    """Matches are advisory only: never use them to choose a write destination."""
    for match in sorted(
        bookmark_token(p.relative_to(store.root)).as_posix() for p in paths if p != destination
    ):
        print(
            f"bm: warning: equivalent URL already stored at {match!r}; "
            "use 'bm show <path>' or 'bm open <path>' to inspect it. "
            "Saved separately; existing bookmark left unchanged.",
            file=sys.stderr,
        )


def _save_edited_capture(
    store: Store, path: Path, slug: str, meta: Metadata, body: str, *, force: bool
) -> str:
    from .editor import edited_bookmark

    store.check_destination(path)
    original = None
    if force:
        try:
            original = store.snapshot(path)
        except FileNotFoundError:
            pass

    template = build_text(meta, body).encode("utf-8")
    with edited_bookmark(store.root, path, template) as (edited, body):
        edited["created"] = meta["created"]
        content = build_text(edited, body)
        store.check_destination(path)
        if original is not None:
            store.write(path, content, expected=original)
        else:
            # Even --force must not replace a file created while the editor was open.
            try:
                store.create_entry(path, content)
            except FileExistsError as exc:
                raise ConflictError(f"bookmark appeared while editing: {path}") from exc
    url = metadata_text(edited, "url")
    if url != meta["url"]:
        print(
            f"bm: warning: url changed in editor; entry stored under original slug "
            f"({slug}). Use `bm mv` to relocate.",
            file=sys.stderr,
        )
    return url


def _matches_capture(store: Store, path: Path, content: str) -> bool:
    """Compare the actual destination, not the advisory duplicate-URL map."""
    store.check_destination(path)
    entry = store.read_entry(path, snapshot=True)
    requested, body = parse_front_matter(content)
    return (
        metadata_text(entry.meta, "url") == metadata_text(requested, "url")
        and metadata_text(entry.meta, "title") == metadata_text(requested, "title")
        and metadata_tags(entry.meta) == metadata_tags(requested)
        and entry.body.replace("\r\n", "\n") == body.replace("\r\n", "\n")
    )


def _create_capture(
    store: Store, path: Path, content: str, collision: str, *, resume: bool
) -> bool:
    """Create atomically or skip a verified match. A different occupant always wins."""
    if not path.exists():
        try:
            store.create_entry(path, content)
            return True
        except FileExistsError:
            pass  # A concurrent creator won; inspect the live destination below.
    if resume:
        if _matches_capture(store, path, content):
            return False
        collision += " (different URL, title, tags, or notes; not overwritten)"
    raise BmError(collision)


def _save_capture(
    store: Store, args: Namespace, url: str, *, resume: bool = False
) -> tuple[Path, str, bool]:
    """Return the destination, stored URL, and whether this call saved it."""
    slug = args.id or create_slug_from_url(url)
    if args.path:
        folder = normalize_slug(reject_unsafe(args.path))
        slug = f"{folder}/{normalize_slug(reject_unsafe(slug))}"
    slug = reject_unsafe(slug)
    fpath = store.path_for(slug)
    collision = f"bookmark exists: {slug}"
    if getattr(args, "batch", None) is None:
        collision += " (use --force to overwrite)"

    meta: Metadata = {
        "url": url,
        "title": args.name or "",
        "tags": [t.strip() for t in (args.tags or "").split(",") if t.strip()],
        "created": iso_now(),
    }
    body = (args.description or "").rstrip()
    if body:
        body += "\n"

    if args.edit:
        if not args.force and fpath.exists():
            raise BmError(collision)
        return fpath, _save_edited_capture(store, fpath, slug, meta, body, force=args.force), True

    content = build_text(meta, body)
    if args.force:
        store.write(fpath, content)
        return fpath, url, True
    return fpath, url, _create_capture(store, fpath, content, collision, resume=resume)


def _add_batch(
    store: Store, args: Namespace, source: str, existing_keys: Iterable[tuple[str, Path]]
) -> None:
    resume = getattr(args, "resume", False)
    stdin = source == "-"
    label = "stdin" if stdin else source
    known: defaultdict[str, set[Path]] = defaultdict(set)
    saved = skipped = 0
    input_stream = nullcontext(sys.stdin) if stdin else Path(source).open(encoding="utf-8")
    with input_stream as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                url = _validated_capture_url(line, "batch line")
                path, url, wrote = _save_capture(store, args, url, resume=resume)
            except (BmError, OSError, UnicodeError) as exc:
                raise BmError(
                    f"{label}:{number}: {exc}. Batch stopped; bookmarks saved: {saved}.",
                    exit_code=exc.exit_code if isinstance(exc, BmError) else 2,
                ) from exc
            if not wrote:
                skipped += 1
                continue
            if not saved:
                for key, existing in existing_keys:
                    known[key].add(existing)
            if key := _capture_key(url):
                _warn_existing_urls(store, path, known[key])
                known[key].add(path)
            saved += 1
            print(rid(url))
    if skipped:
        print(f"bm: skipped matching batch entries: {skipped}", file=sys.stderr)
    elif not saved:
        raise BmError(f"{label}: no URLs to add")


def add_bookmarks(
    store: Store, args: Namespace, *, on_error: Callable[[Path, Exception], None]
) -> None:
    """Capture one URL or a batch. Consult existing headers only after the first save."""
    store.require_exists(f"store not found: {store.root}. Run `bm init` first.")
    existing = _stored_url_keys(store.iter_entries(meta_only=True, on_error=on_error))
    batch = getattr(args, "batch", None)
    if batch is not None:
        if args.id is not None or args.edit or args.force:
            raise BmError("--batch cannot be combined with --id, --edit, or --force", exit_code=2)
        _add_batch(store, args, batch, existing)
        return
    if getattr(args, "resume", False):
        raise BmError("--resume requires --batch", exit_code=2)
    url = _add_url(args.url, clipboard=getattr(args, "clipboard", False))
    path, url, _ = _save_capture(store, args, url)
    if key := _capture_key(url):
        _warn_existing_urls(store, path, (p for other_key, p in existing if other_key == key))
    print(rid(url))
