"""Command line interface for the bookmark manager."""

import argparse

from .commands import (
    cmd_add,
    cmd_dedupe,
    cmd_dirs,
    cmd_edit,
    cmd_export,
    cmd_import,
    cmd_init,
    cmd_list,
    cmd_mv,
    cmd_open,
    cmd_rm,
    cmd_search,
    cmd_show,
    cmd_sync,
    cmd_tag,
    cmd_tags,
)


def main() -> None:
    """Main entry point."""
    ap = argparse.ArgumentParser(prog="bm", description="Plain-text, pass-style bookmarks")
    ap.add_argument(
        "--store",
        help="Path to bookmark store (default: $BOOKMARKS_DIR or ~/.bookmarks.d)",
    )
    sp = ap.add_subparsers(dest="cmd", required=True)

    p = sp.add_parser("init", help="Create a new store")
    p.add_argument("--git", action="store_true", help="Initialize a git repo in the store")
    p.set_defaults(func=cmd_init)

    p = sp.add_parser("add", help="Add a bookmark")
    p.add_argument("url")
    p.add_argument("-n", "--name", help="Title")
    p.add_argument("-t", "--tags", help="Comma-separated tags")
    p.add_argument("-d", "--description", help="Notes / description")
    p.add_argument("-p", "--path", help="Folder path like dev/python")
    p.add_argument("--id", help="Explicit id/slug (relative path ok)")
    p.add_argument("--edit", action="store_true", help="Open $EDITOR with a prefilled template")
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
    p.add_argument("--path", help="Filter by path prefix (e.g., dev/python)")
    p.add_argument("--json", action="store_true", help="Emit JSON array")
    p.add_argument("--jsonl", action="store_true", help="Emit JSON Lines (NDJSON)")
    p.set_defaults(func=cmd_list)

    p = sp.add_parser("search", help="Full-text search over title/url/tags/body")
    p.add_argument("query")
    p.add_argument("--path", help="Filter by path prefix (e.g., dev/python)")
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

    p = sp.add_parser("dirs", help="List known directory prefixes")
    p.add_argument("--json", action="store_true", help="Emit JSON array")
    p.set_defaults(func=cmd_dirs)

    p = sp.add_parser("dedupe", help="Merge duplicate bookmarks by normalized URL")
    p.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    p.add_argument("--json", action="store_true", help="Emit JSON summary")
    p.set_defaults(func=cmd_dedupe)

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
