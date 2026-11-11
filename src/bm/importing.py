"""Best-effort browser import workflow, independent of CLI presentation."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .errors import UnsafePathError
from .io import build_text
from .models import Metadata, metadata_text
from .netscape import parse_netscape_document
from .store import Store
from .urls import inspect_url
from .utils import create_slug_from_url, iso_now


@dataclass
class ImportCounts:
    """Record actions, not unique destinations; shared by preview and execution."""

    added: int = 0
    replaced: int = 0
    skipped_existing: int = 0
    invalid_scheme: int = 0
    invalid_url: int = 0
    unsafe: int = 0
    missing_href: int = 0

    @property
    def invalid(self) -> int:
        return self.invalid_scheme + self.invalid_url + self.unsafe + self.missing_href


def _import_path(
    store: Store,
    folder: str,
    meta: Metadata,
    counts: ImportCounts,
    on_warning: Callable[[str], None] | None,
) -> Path | None:
    """Validate a browser record and retain diagnostic counts for previews."""
    inspection = inspect_url(metadata_text(meta, "url"))
    if inspection.problem:
        counts.invalid_url += 1
        if on_warning:
            on_warning(f"skipped invalid import URL: {inspection.problem}")
        return None
    if not inspection.supported:
        counts.invalid_scheme += 1
        return None
    slug = create_slug_from_url(metadata_text(meta, "url"))
    full_path = f"{folder}/{slug}" if folder else slug
    try:
        destination = store.path_for(full_path)
        store.check_destination(destination)
        return destination
    except UnsafePathError as exc:
        if on_warning:
            on_warning(f"skipped import path {full_path!r}: {exc}")
        counts.unsafe += 1
        return None


def import_netscape(
    store: Store,
    text: str,
    *,
    force: bool = False,
    dry_run: bool = False,
    on_warning: Callable[[str], None] | None = None,
    on_progress: Callable[[int], None] | None = None,
) -> ImportCounts:
    """Import best-effort, or preview identical record actions without writes."""
    entries, missing_href = parse_netscape_document(text, default_created=iso_now())
    counts = ImportCounts(missing_href=missing_href)
    if not dry_run:
        store.create()
    seen_paths: set[Path] = set()
    for folder, meta in entries:
        fpath = _import_path(store, folder, meta, counts, on_warning)
        if fpath is None:
            continue
        exists = fpath.exists() or fpath in seen_paths
        if exists and not force:
            counts.skipped_existing += 1
            continue
        if not dry_run:
            try:
                if force:
                    store.write(fpath, build_text(meta, ""))
                else:
                    store.create_entry(fpath, build_text(meta, ""))
            except FileExistsError:
                counts.skipped_existing += 1
                continue
        seen_paths.add(fpath)
        if exists:
            counts.replaced += 1
        else:
            counts.added += 1
        if not dry_run and on_progress:
            on_progress(counts.added + counts.replaced)

    return counts
