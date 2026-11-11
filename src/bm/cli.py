"""Command line interface for the bookmark manager."""

# PYTHON_ARGCOMPLETE_OK
import argparse
import errno
import os
import stat
import sys
from contextlib import redirect_stdout
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Optional, Sequence, TextIO

from .errors import BmError


def _add_filter_flags(parser: argparse.ArgumentParser) -> None:
    """Attach the standard filter flags (tag/host/since/path) to a subparser."""
    parser.add_argument("-t", "--tag", help="Filter by tag (folder segment or header tag)")
    parser.add_argument("--host", help="Filter by URL host (exact, 'www.' ignored)")
    parser.add_argument("--since", help="ISO date/time or YYYY-MM-DD (lower bound)")
    parser.add_argument("--path", help="Filter by path prefix (e.g., dev/python)")


def _add_command_options(
    commands: Sequence[argparse.ArgumentParser], formats: Sequence[argparse.ArgumentParser]
) -> None:
    """Accept common options at nested levels without resetting parent values."""
    for parser in [*commands, *formats]:
        parser.add_argument("--store", default=argparse.SUPPRESS, help="Path to bookmark store")
    for parser in formats:
        parser.add_argument(
            "-o",
            "--output",
            default=argparse.SUPPRESS,
            help="Write to a new file (never overwrite)",
        )


def _parse_arguments(
    parser: argparse.ArgumentParser, argv: Optional[Sequence[str]]
) -> argparse.Namespace:
    """Accept trailing positional search terms without swallowing unknown options.

    Subparsers cannot use argparse's intermixed parser. Ordinary search is AND-based,
    so appending positional terms left after options preserves its semantics. Regex
    still requires one argument, and -- retains its literal-argument meaning.
    """
    args, extra = parser.parse_known_args(argv)
    if not extra:
        return args
    if args.cmd != "search":
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    boundary = extra.index("--") if "--" in extra else len(extra)
    if any(token.startswith("-") for token in extra[:boundary]):
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    args.query.extend(extra[:boundary])
    args.query.extend(extra[boundary + 1 :])
    return args


def _exit_code(value: object) -> int:
    """Convert ``SystemExit.code`` to a process status without raising it."""
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    print(value, file=sys.stderr)
    return 1


class _WindowsPipeWriter:
    """Translate Windows pipe EINVAL only at the stdout write/flush boundary."""

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)

    def write(self, text: str) -> int:
        try:
            return self._stream.write(text)
        except OSError as exc:
            if exc.errno == errno.EINVAL:
                raise BrokenPipeError(errno.EPIPE, "stdout pipe closed") from exc
            raise

    def flush(self) -> None:
        try:
            self._stream.flush()
        except OSError as exc:
            if exc.errno == errno.EINVAL:
                raise BrokenPipeError(errno.EPIPE, "stdout pipe closed") from exc
            raise


def _stdout_is_windows_pipe() -> bool:
    """Restrict EINVAL translation to a real Windows stdout pipe, not files."""
    if sys.platform != "win32":
        return False
    try:
        return stat.S_ISFIFO(os.fstat(sys.stdout.fileno()).st_mode)
    except (OSError, ValueError):
        return False


def _flush_stdout(status: int) -> int:
    """Flush command output and turn a closed downstream pipe into quiet success."""
    try:
        sys.stdout.flush()
    except BrokenPipeError:
        # Replace the broken descriptor so interpreter shutdown cannot retry the
        # failed flush and override the intended zero status with exit 120.
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
            os.close(devnull)
        except (OSError, ValueError):
            pass
        return 0
    return status


def _run_command(args: argparse.Namespace) -> int:
    """Dispatch parsed arguments and translate command-level failures."""
    try:
        from . import commands

        getattr(commands, args.func)(args)
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        # Downstream pipe closed (e.g. `bm list | head`). Quiet success.
        return 0
    except BmError as exc:
        if str(exc):
            print(f"bm: {exc}", file=sys.stderr)
        return exc.exit_code
    except Exception as exc:
        if os.environ.get("BM_DEBUG"):
            raise
        print(f"bm: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the CLI and return its process status.

    ``argv`` excludes the executable name, matching ``argparse`` conventions.
    Help and parser errors are handled here so embedding code can call ``main``
    without catching ``SystemExit``; the console script and module entry point
    remain responsible for turning the returned status into process exit.
    """

    first_steps = (
        "Getting started:\n"
        "  bm init\n"
        '  bm add https://example.com --name "Example"\n'
        "  bm search example\n"
        "\nRun bm <command> --help for command details."
    )
    ap = argparse.ArgumentParser(
        prog="bm",
        description="Plain-text, pass-style bookmarks",
        epilog=first_steps,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    try:
        package_version = version("bkmrk")
    except PackageNotFoundError:
        package_version = "unknown (package not installed)"
    ap.add_argument("--version", action="version", version=f"%(prog)s {package_version}")
    ap.add_argument(
        "--store",
        help="Path to bookmark store (default: $BOOKMARKS_DIR or ~/.bookmarks.d)",
    )
    sp = ap.add_subparsers(dest="cmd", required=True)

    p = sp.add_parser("init", help="Create a new store")
    p.add_argument("--git", action="store_true", help="Initialize a git repo in the store")
    p.set_defaults(func="cmd_init")

    p = sp.add_parser("add", help="Add a bookmark")
    p.add_argument("url", help="URL, or - to read exactly one URL from stdin")
    p.add_argument("-n", "--name", help="Title")
    p.add_argument("-t", "--tags", help="Comma-separated tags")
    p.add_argument("-d", "--description", help="Notes / description")
    p.add_argument("-p", "--path", help="Folder path like dev/python")
    p.add_argument("--id", help="Explicit id/slug (relative path ok)")
    p.add_argument("--edit", action="store_true", help="Open $EDITOR with a prefilled template")
    p.add_argument("-f", "--force", action="store_true", help="Overwrite if exists")
    p.set_defaults(func="cmd_add")

    p = sp.add_parser("show", help="Show an entry")
    p.add_argument("id", help="Stable ID or path/slug")
    p.set_defaults(func="cmd_show")

    p = sp.add_parser("open", help="Open in browser")
    p.add_argument("id", help="Stable ID or path/slug")
    p.add_argument(
        "--allow-scheme",
        action="store_true",
        help="Open URL even when its scheme is outside the safe allow-list",
    )
    p.set_defaults(func="cmd_open")

    p = sp.add_parser("list", help="List all entries")
    _add_filter_flags(p)
    output = p.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="Emit JSON array")
    output.add_argument("--jsonl", action="store_true", help="Emit JSON Lines (NDJSON)")
    p.set_defaults(func="cmd_list")

    p = sp.add_parser(
        "search",
        help="Substring-AND search across title/url/tags/body (or --regex)",
        description=(
            "Default: case-insensitive substring AND across all four fields. "
            "Use --regex for a Python regex (case-insensitive); --field to scope. "
            "Exits 1 when no entries match."
        ),
    )
    p.add_argument("query", nargs="+", help="Search terms (quote a --regex pattern)")
    _add_filter_flags(p)
    p.add_argument(
        "--regex",
        action="store_true",
        help="Treat query as a case-insensitive Python regex",
    )
    p.add_argument(
        "--field",
        action="append",
        choices=["title", "url", "tags", "body"],
        help="Restrict search to a field (repeatable; default: all four)",
    )
    output = p.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="Emit JSON array")
    output.add_argument("--jsonl", action="store_true", help="Emit JSON Lines (NDJSON)")
    p.set_defaults(func="cmd_search")

    p = sp.add_parser("pick", help="Select a bookmark using optional fzf (opens by default)")
    _add_filter_flags(p)
    p.add_argument(
        "--print",
        dest="print_field",
        choices=["id", "url"],
        help="Print the selected ID or URL instead of opening a browser",
    )
    p.set_defaults(func="cmd_pick")

    p = sp.add_parser("edit", help="Edit with $EDITOR / $VISUAL")
    p.add_argument("id")
    p.set_defaults(func="cmd_edit")

    p = sp.add_parser("rm", help="Remove an entry")
    p.add_argument("id")
    p.set_defaults(func="cmd_rm")

    p = sp.add_parser("mv", help="Rename/move an entry")
    p.add_argument("src")
    p.add_argument("dst")
    p.add_argument("-f", "--force", action="store_true")
    p.set_defaults(func="cmd_mv")

    p = sp.add_parser("tags", help="List all discovered tags")
    p.set_defaults(func="cmd_tags")

    p = sp.add_parser("dirs", help="List known directory prefixes")
    p.add_argument("--json", action="store_true", help="Emit JSON array")
    p.set_defaults(func="cmd_dirs")

    p = sp.add_parser("dedupe", help="Merge duplicate bookmarks by normalized URL")
    p.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    p.add_argument("--json", action="store_true", help="Emit JSON summary")
    p.set_defaults(func="cmd_dedupe")

    p = sp.add_parser("doctor", help="Check store health without changing files")
    p.add_argument("--json", action="store_true", help="Emit a structured diagnostic report")
    p.set_defaults(func="cmd_doctor")

    p = sp.add_parser("tag", help="Mutate tags without editing")
    p.add_argument("action", choices=["add", "rm"])
    p.add_argument("id")
    p.add_argument("tags", nargs="+")
    p.set_defaults(func="cmd_tag")

    p = sp.add_parser(
        "export",
        help="Export a complete backup or browser/pipeline data",
        description=(
            "Export a complete lossless JSON backup by default. Write to stdout, or use "
            "-o FILE to safely create a new file in any format. Explicit netscape/json formats are "
            "browser/pipeline exports, not complete backups."
        ),
    )
    p.add_argument("-o", "--output", help="Write to a new file (never overwrite)")
    p.set_defaults(func="cmd_export")
    spx = p.add_subparsers(dest="fmt")
    pe = spx.add_parser("netscape", help="Export as Netscape bookmarks HTML")
    _add_filter_flags(pe)
    pe.set_defaults(func="cmd_export")
    pj = spx.add_parser("json", help="Export as JSON array")
    _add_filter_flags(pj)
    pj.add_argument(
        "--jsonl",
        action="store_true",
        help="Stream JSON Lines (NDJSON) instead of a single sorted array",
    )
    pj.set_defaults(func="cmd_export")

    p = sp.add_parser(
        "import",
        help="Import a backup or browser bookmarks",
        description=(
            "Recognize a lossless JSON backup or Netscape browser HTML automatically. "
            "Backup imports validate all records before writing, leave identical files "
            "alone, and stop on conflicting paths unless --force is given."
        ),
    )
    p.add_argument("file", help="Backup/browser export file, or - to read stdin")
    p.add_argument(
        "-f", "--force", action="store_true", help="Replace matching paths, keep unrelated files"
    )
    p.add_argument("--dry-run", action="store_true", help="Preview without filesystem changes")
    p.set_defaults(func="cmd_import")

    p = sp.add_parser("sync", help="git add/commit/push if repo")
    p.set_defaults(func="cmd_sync")

    _add_command_options(list(sp.choices.values()), (pe, pj))

    try:
        # Optional shell completion (no hard dependency).
        try:
            import argcomplete
        except ImportError:
            pass
        else:
            argcomplete.autocomplete(ap)
        args = _parse_arguments(ap, argv)
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0
    except SystemExit as exc:
        # argparse uses SystemExit for --help and usage errors. Preserve those
        # statuses and output while making the function directly callable.
        if exc.code == 2 and not (sys.argv[1:] if argv is None else argv):
            print(f"\n{first_steps}", file=sys.stderr)
        return _exit_code(exc.code)

    if _stdout_is_windows_pipe():
        with redirect_stdout(_WindowsPipeWriter(sys.stdout)):
            return _flush_stdout(_run_command(args))
    return _flush_stdout(_run_command(args))
