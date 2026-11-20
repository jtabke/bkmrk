"""Single and batch capture with advisory duplicate warnings and checked publication."""

import os
import sys
import tempfile
from argparse import Namespace
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from contextlib import nullcontext
from pathlib import Path

from .dedupe import duplicate_key
from .errors import BmError
from .io import build_text, parse_for_rewrite
from .models import Metadata, metadata_text
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


def _edit_template(destination: Path, meta: Metadata, body: str) -> tuple[Metadata, str]:
    from .editor import launch_editor

    fd, tmp_name = tempfile.mkstemp(suffix=".bm", prefix="bm-")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(build_text(meta, body))
        launch_editor(tmp)
        edited, body = parse_for_rewrite(tmp.read_bytes(), destination)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    if not metadata_text(edited, "url"):
        raise BmError("url cleared in editor")
    edited["created"] = meta["created"]
    return edited, body


def _save_capture(store: Store, args: Namespace, url: str) -> tuple[Path, str]:
    """Save one bookmark through the same checked create/replace path for all inputs."""
    slug = args.id or create_slug_from_url(url)
    if args.path:
        folder = normalize_slug(reject_unsafe(args.path))
        slug = f"{folder}/{normalize_slug(reject_unsafe(slug))}"
    slug = reject_unsafe(slug)
    fpath = store.path_for(slug)
    collision = f"bookmark exists: {slug}"
    if getattr(args, "batch", None) is None:
        collision += " (use --force to overwrite)"
    if fpath.exists() and not args.force:
        raise BmError(collision)

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
        meta, body = _edit_template(fpath, meta, body)
        if meta["url"] != url:
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
        raise BmError(collision) from exc
    return fpath, metadata_text(meta, "url")


def _add_batch(
    store: Store, args: Namespace, source: str, existing_keys: Iterable[tuple[str, Path]]
) -> None:
    if args.id is not None or args.edit or args.force:
        raise BmError("--batch cannot be combined with --id, --edit, or --force", exit_code=2)
    stdin = source == "-"
    label = "stdin" if stdin else source
    known: defaultdict[str, set[Path]] = defaultdict(set)
    saved = 0
    input_stream = nullcontext(sys.stdin) if stdin else Path(source).open(encoding="utf-8")
    with input_stream as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                url = _validated_capture_url(line, "batch line")
                path, url = _save_capture(store, args, url)
            except (BmError, OSError, UnicodeError) as exc:
                raise BmError(
                    f"{label}:{number}: {exc}. Batch stopped; bookmarks saved: {saved}.",
                    exit_code=exc.exit_code if isinstance(exc, BmError) else 2,
                ) from exc
            if not saved:
                for key, existing in existing_keys:
                    known[key].add(existing)
            if key := _capture_key(url):
                _warn_existing_urls(store, path, known[key])
                known[key].add(path)
            saved += 1
            print(rid(url))
    if not saved:
        raise BmError(f"{label}: no URLs to add")


def add_bookmarks(
    store: Store, args: Namespace, *, on_error: Callable[[Path, Exception], None]
) -> None:
    """Capture one URL or a batch. Consult existing headers only after the first save."""
    store.require_exists(f"store not found: {store.root}. Run `bm init` first.")
    existing = _stored_url_keys(store.iter_entries(meta_only=True, on_error=on_error))
    batch = getattr(args, "batch", None)
    if batch is not None:
        _add_batch(store, args, batch, existing)
        return
    url = _add_url(args.url, clipboard=getattr(args, "clipboard", False))
    path, url = _save_capture(store, args, url)
    if key := _capture_key(url):
        _warn_existing_urls(store, path, (p for other_key, p in existing if other_key == key))
    print(rid(url))
