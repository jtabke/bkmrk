# bm — plain‑text bookmarks

[![Tests][tests-badge]][tests-workflow]

**Portable, file-based bookmarks for the command line.**

Each bookmark is a readable `.bm` file containing metadata and freeform notes.

```console
$ bm init
Initialized store at: /path/to/your/home/.bookmarks.d

$ bm add https://github.com/jtabke/bkmrk \
    --name "bkmrk on GitHub" \
    --tags cli,bookmarks,python \
    --description "Source and documentation for bm." \
    --id tools/bkmrk
f58e60347098

$ bm search "source documentation"
f58e60347098  tools/bkmrk — bkmrk on GitHub <https://github.com/jtabke/bkmrk>
```

The initialization path above is illustrative; `bm` prints your actual home directory.
Check the installed version with `bm --version`.

The bookmark is just a file:

```text
~/.bookmarks.d/tools/bkmrk.bm

---
url: https://github.com/jtabke/bkmrk
title: bkmrk on GitHub
tags: [cli, bookmarks, python]
created: 2025-09-16T08:42:00-07:00
---
Source and documentation for bm.
```

Files can be searched, edited, copied, versioned with Git, or synchronized using any
filesystem tool.

> Works on macOS, Linux, WSL, and Windows (PowerShell). The runtime has no
> third-party dependencies.

---

## Table of contents

- [Why bm?](#why-bm)
- [Install](#install)
- [Quickstart](#quickstart)
- [Concepts](#concepts)
  - [Store layout](#store-layout)
  - [Bookmark file format](#bookmark-file-format)
  - [IDs](#ids)

- [CLI usage](#cli-usage)
  - [`init`](#init)
  - [`add`](#add)
  - [`list`](#list)
  - [`search`](#search)
  - [`show` and `open`](#show-and-open)
  - [`edit`, `rm`, `mv`](#edit-rm-mv)
  - [`tags` and `tag add|rm`](#tags-and-tag-addrm)
  - [`dirs`](#dirs)
  - [`dedupe`](#dedupe)
  - [`export` and `import`](#export-and-import)
  - [`sync`](#sync)

- [Filtering & output formats](#filtering--output-formats)
- [Integration recipes](#integration-recipes)
- [Configuration](#configuration)
- [Security & robustness](#security--robustness)
- [Development](#development)
- [License](#license)

---

## Why bm?

Most bookmark tools are databases or browser‑locked. `bm` chooses **text first**: plain UTF‑8 files that last decades, are easy to diff, and play well with your editor, shell, and Git. It embraces "do one thing well" and stays small so you can integrate it anywhere.

---

## Install

Requires Python >=3.12. The next release drops support for Python 3.8–3.11;
users on those versions should stay on an older compatible release. Install from PyPI:

```bash
pip install bkmrk
```

For an isolated CLI installation with pipx:

```bash
pipx install bkmrk
```

Alternatively, using uv to install as a tool:

```bash
uv tool install bkmrk
```

For the latest development version, clone and install locally:

```bash
git clone https://github.com/jtabke/bkmrk
cd bkmrk
python -m pip install .
```

Or with uv:

```bash
git clone https://github.com/jtabke/bkmrk
cd bkmrk
uv pip install .
```

Development workflows can pull in the optional extras declared in `pyproject.toml`:

```bash
python -m pip install -e '.[dev]'
```

Or with uv:

```bash
uv sync --locked --extra dev
```

Prefer running straight from the repository without installation? Point Python at
the `src` layout:

```bash
PYTHONPATH=src python -m bm --help
```

On Windows (PowerShell):

```powershell
$env:PYTHONPATH = "src"; python -m bm --help
```

---

## Quickstart

```bash
# initialize a new store (optionally a git repo)
bm init --git

# import bookmarks from a browser export (Netscape HTML)
bm import ~/Downloads/bookmarks.html

# add a bookmark
bm add https://example.com -n "Example" -t ref,demo -d "Short note"

# list newest bookmarks (ID, path, title, URL)
bm list

# search across title/url/tags/body
bm search kernel

# search within a specific path
bm search kernel --path dev/linux

# list directory prefixes
bm dirs

# open the first result
ID=$(bm search kernel --jsonl | head -1 | jq -r '.id')
bm open "$ID"

# export for browsers (Netscape HTML)
bm export netscape > bookmarks.html
```

---

## Concepts

### Store layout

Default store directory is `~/.bookmarks.d` (override via `$BOOKMARKS_DIR`). Each bookmark is a single `.bm` file; directories serve as namespaces.

```
~/.bookmarks.d/
  dev/python/fastapi-3a1b2c4.bm
  news-ycombinator-com-1234567.bm
  README.txt
```

Auto-generated file names are **human readable** and end with a **short hash of the URL** to avoid collisions. Explicit `--id` values and destinations supplied to `bm mv` are used instead and do not need to contain a hash.

### Bookmark file format

Each `.bm` file contains front matter and an optional body:

```text
---
url: https://example.com/blog/post
title: Great post
tags: [read, blog, "needs,comma"]
created: 2025-09-16T08:42:00-07:00
modified: 2025-09-17T09:10:00-07:00
---
Longer notes, checklists, code blocks…
```

- `tags` is a list and supports quoting for commas/spaces
- Any extra keys are preserved on round‑trip

### IDs

Each bookmark has a **stable ID** derived from its URL (BLAKE2b short hash). The ID does **not** change if you rename/move the file. You can use either the ID **or** a path‑like slug with commands.

### Architecture

The implementation keeps the filesystem backend concrete and separates responsibilities by seam:

- `bm.cli`: parser construction, `main(argv=None) -> int`, and process-boundary error/status handling
- `bm.commands`: thin command orchestration and user-facing output
- `bm.store`: safe path resolution, entry traversal, snapshots, and filesystem mutations
- `bm.io`: front matter parsing/rendering and atomic file replacement
- `bm.query`: immutable filter values and list/search row logic
- `bm.netscape`: Netscape HTML import/export conversion
- `bm.backup`: versioned exact-content backups and restore preflight
- `bm.dedupe`: pure duplicate selection and merge rules

Embedding code can call `bm.cli.main([...])` and inspect the returned status without catching `SystemExit`. The console script and `python -m bm` wrappers convert that status to the process exit code. Argument-parser help and usage errors retain their standard 0/2 statuses.

---

## CLI usage

Run `bm --help` or `bm <command> --help` for command details.

### `init`

Create a store; optional `--git` initializes a Git repo.

```bash
bm init --git
```

### `add`

Add a bookmark. `--edit` opens your `$EDITOR` with a pre‑filled template.

```bash
bm add <url> [-n TITLE] [-t tag1,tag2] [-d NOTES] [-p dir1/dir2] [--id SLUG] [--edit] [-f]
```

Prints the stable ID on success.

### `list`

List bookmarks (newest first).

```bash
bm list [--host HOST] [--since ISO|YYYY-MM-DD] [-t TAG] [--path PREFIX] [--json|--jsonl]
```

### `search`

Default semantics: case-insensitive substring **AND** across title, URL, tags, and body. The query is split on whitespace, and each term must appear somewhere in the combined fields. Because `<query>` is one positional argument, quote any multiword query so the shell passes it as one value. Use `--regex` when you need phrase or pattern matching.

```bash
bm search <query> [-t TAG] [--host HOST] [--since ISO|YYYY-MM-DD] [--path PREFIX] [--regex] [--field FIELD] [--json|--jsonl]
```

- `--regex` treats the query as a Python regex (case‑insensitive).
- `--field {title,url,tags,body}` restricts the scope; repeat to search multiple fields.
- Exits **0** when at least one match is printed, **1** when there are no matches (so `bm search foo && open …` works as expected).

### `show` and `open`

Display metadata/notes or open the URL in your default browser:

```bash
bm show <ID|path>
bm open <ID|path> [--allow-scheme]
```

`open` accepts `http`, `https`, `ftp`, `ftps`, and `mailto` URLs by default. Use `--allow-scheme` only when you intend to pass another scheme to the operating system.

### `edit`, `rm`, `mv`

```bash
bm edit <ID|path>   # bumps modified timestamp
bm rm <ID|path>
bm mv <SRC> <DST> [-f]
```

### `tags` and `tag add|rm`

List discovered tags (from folder segments and header tags), or mutate tags without opening an editor.

```bash
bm tags
bm tag add <ID|path> tag1 tag2
bm tag rm  <ID|path> tag1
```

### `dirs`

List all known directory prefixes in the bookmark store.

```bash
bm dirs [--json]
```

### `dedupe`

Merge duplicate bookmarks that resolve to the same normalized URL. The command unions
tags (including folder segments), keeps the most informative entry, and appends any extra
notes with provenance markers.

```bash
bm dedupe [--dry-run] [--json]
```

- `--dry-run` prints the planned merges without modifying files
- `--json` emits a machine-readable summary of the merge actions

URL normalization for duplicate detection:

- Lowercases the scheme/host and strips only a leading `www.` host label.
- Treats `http://` and `https://` versions of the same URL as equivalent.
- Removes default ports (`:80` for HTTP/schemeless URLs, `:443` for HTTPS), but preserves
  non-default ports.
- Collapses duplicate path slashes, resolves `.`/`..` path segments, and ignores trailing `/`.
- Drops fragments (`#section`).
- Preserves path params (`;param`) and userinfo (`user:pass@host`) in the dedupe key.
- Sorts query parameters while preserving duplicate keys and blank values.
- Treats host-like schemeless inputs such as `example.com/path` and `localhost:8000/path` as
  web URLs.

Auto-generated bookmark slugs use similar host/path parsing, but exclude userinfo so credentials
are not written into filenames.

### `export` and `import`

**Plain export is a complete, lossless bookmark backup.** Import recognizes backups and
Netscape browser HTML automatically.

```bash
bm export -o bookmarks.json             # Safely create a new backup file
bm export > bookmarks.json             # Same JSON to stdout; shell redirection can overwrite
bm import bookmarks.json --dry-run     # Validate and preview without filesystem changes
bm import bookmarks.json               # Restore missing bookmarks; leave identical ones alone
bm import bookmarks.json --force       # Also replace conflicting paths, never delete unrelated files
```

`-o` / `--output` is for complete backups: omit the format and place the option after
`export`. It refuses existing output files and publishes the completed backup atomically.
The output directory must already exist. Backups include every `.bm` file, preserving exact
paths and UTF-8 content: URLs, titles, tags, timestamps, unknown metadata, comments, notes,
and line endings. They do not include Git history/configuration, other store files, file
permissions, or filesystem timestamps. Symlinks and unreadable/non-UTF-8 bookmarks cause
export to fail rather than silently produce an incomplete backup. Backup exports do not
accept filters.

Backup import validates every record before writing, rejecting malformed records,
unsupported versions, duplicate/overlapping destinations, unsafe paths, symlinks, and
missing or unsupported URL schemes. Any differing existing bookmark blocks the entire
preflight unless `--force` is given. `--dry-run` lists conflicts and reports deterministic
counts without creating the store; conflicts return exit status 1 even during a preview.
Identical files are left untouched. Validation failures write nothing; an I/O failure or
concurrent edit during writing can leave earlier files restored. Each file is published
atomically, new files never clobber concurrent creations, and replacements check the
preflight byte snapshot. This is not a whole-store transaction or a snapshot of concurrent
store edits. See the [backup format and recovery contract](docs/backup-format.md).

Existing browser and pipeline exports remain unchanged:

```bash
bm export netscape [-t TAG] [--host HOST] [--since ISO|YYYY-MM-DD] [--path PREFIX] > bookmarks.html
bm export json [-t TAG] [--host HOST] [--since ISO|YYYY-MM-DD] [--path PREFIX] [--jsonl] > dump.json
bm import bookmarks.html [-f] [--dry-run]
```

`export json` / JSONL is a metadata-only pipeline format, **not a restorable backup**.
Netscape import preserves folder hierarchies and retains its best-effort skip behavior;
its dry-run reports how many bookmarks would be imported and any skipped invalid entries.

### `sync`

If the store is a Git repo, stage/commit and (if upstream exists) push.

```bash
bm sync
```

---

## Filtering & output formats

- `--host` matches the parsed URL authority exactly (case-insensitive, ignoring a leading `www.`). Include an explicit port when the saved URL has one.
- `--path` filters by path prefix (e.g., `--path dev/python` shows only entries under that directory tree)
- `--since` accepts `YYYY-MM-DD` or full ISO timestamps; comparisons are proper datetimes
- `--json` emits a single JSON array; `--jsonl` outputs one JSON object per line (NDJSON)

Common JSON schema fields: `id`, `path`, `title`, `url`, `tags`, `created`, `modified`.

---

## Integration recipes

**fzf launcher**

```bash
choice="$(
    bm list --jsonl |
    jq -r '[.id, .title, .url] | @tsv' |
    fzf --with-nth=2..
)"
if [ -n "$choice" ]; then
    bm open "$(printf "%s" "$choice" | cut -f1)"
fi
```

**Open the latest saved from a host**

```bash
id="$(bm list --host example.com --jsonl | head -1 | jq -r '.id')"
if [ -n "$id" ]; then
    bm open "$id"
fi
```

**Rofi launcher**

```bash
#!/bin/sh
choice="$(
    bm list --jsonl |
    jq -r '.id + "\t" + .title + " — " + .url' |
    rofi -dmenu -i -p "bm"
)"
if [ -n "$choice" ]; then
    bm open "$(printf "%s" "$choice" | cut -f1)"
fi
```

Save as `bm-rofi.sh`, make it executable (`chmod +x bm-rofi.sh`), and bind it to a hotkey. The
tab delimiter keeps IDs intact even when titles contain spaces; rofi shows the full title and URL
while `bm open` receives only the bookmark ID.

**dmenu launcher**

```bash
#!/bin/sh
choice="$(
    bm list --jsonl |
    jq -r '.id + "\t" + .title + " — " + .url' |
    dmenu -l 15 -i -p "bm"
)"
if [ -n "$choice" ]; then
    bm open "$(printf "%s" "$choice" | cut -f1)"
fi
```

You can adjust `-l 15` to change the number of visible rows. Because the script uses tabs between
the ID and the description, `cut -f1` reliably extracts the ID even when titles or URLs contain
spaces.

**Bulk tag HN links**

```bash
bm list --host news.ycombinator.com --jsonl |
jq -r '.id' |
while IFS= read -r id; do
    [ -n "$id" ] && bm tag add "$id" hn
done
```

**List bookmarks in a specific category**

```bash
bm list --path dev/python
bm search "framework" --path dev
```

**Explore directory structure**

```bash
bm dirs
bm dirs --json | jq
```

**Export → browser import**

```bash
bm export netscape > ~/Desktop/bookmarks.html
# Import that file in your browser’s bookmarks manager
```

**Sync with Syncthing**

For cross-device synchronization without Git, use [Syncthing](https://syncthing.net/) to sync your bookmark store:

1. Install Syncthing on all devices.
2. Add your bookmark store directory (`~/.bookmarks.d` or `$BOOKMARKS_DIR`) as a synced folder in Syncthing.
3. Configure devices to share the folder bidirectionally.
4. Syncthing will keep your bookmarks in sync across devices automatically.

**Auto-export for browser import**

To automatically export bookmarks to Netscape HTML for browser import:

```bash
#!/bin/bash
# auto_export.sh
bm export netscape > ~/bookmarks_auto.html
echo "Bookmarks exported to ~/bookmarks_auto.html. Import this file in your browser."
```

Run this script periodically or on demand to generate an up-to-date bookmark file for browser import.

---

## Configuration

- **Store directory**: set `BOOKMARKS_DIR`, or place the global option before the command: `bm --store PATH <command>`
- **Editor**: `VISUAL` or `EDITOR` (supports commands like `code --wait`)
- **Debug**: set `BM_DEBUG=1` to re-raise unexpected exceptions with a full traceback (otherwise printed as a one-line `bm: <Type>: <msg>` to stderr with exit code 2)

### Shell completion

Completion is optional and uses [argcomplete](https://github.com/kislyuk/argcomplete).
Install the extra with your chosen installer:

```bash
pip install 'bkmrk[completion]'
# Or, for an isolated CLI installation:
pipx install 'bkmrk[completion]'
# Or:
uv tool install 'bkmrk[completion]'
```

The registration helper must run in the same environment as `bm`. With pipx, expose
its helper commands using `pipx install --include-deps 'bkmrk[completion]'` instead;
with uv, use `uv tool install --with-executables-from argcomplete 'bkmrk[completion]'`.

For Bash, add this to `~/.bashrc`:

```bash
eval "$(register-python-argcomplete --shell bash bm)"
```

For Zsh, add this to `~/.zshrc` (omit `compinit` if your configuration already runs it):

```zsh
autoload -Uz compinit
compinit
eval "$(register-python-argcomplete --shell zsh bm)"
```

Start a new shell (or reload its configuration), then type `bm se` and press Tab to
complete `search`. This registers only `bm`; global completion activation is not required.
These instructions cover Bash and Zsh, not PowerShell.

### Windows notes

- Bookmark paths accept `/` or `\\` separators on every platform. Commands and JSON/Netscape exports use `/` for consistent logical paths; store and import-file locations remain native filesystem paths.
- Absolute, drive-qualified (including `C:relative`), and parent-traversal bookmark paths are rejected before normalization.
- CI runs the full test suite on Linux, macOS, and Windows with Python 3.12 (the minimum) and 3.15.
- Normalized bookmark filenames are checked for reserved device names (such as `CON`, `NUL`, and `COM1`) and unsupported components on Windows. Invalid imported folder paths are reported and skipped; valid entries still import. Existing POSIX filenames and URL-derived bookmark IDs are unchanged.
- The default Windows editor is Notepad. Set `VISUAL` or `EDITOR` to an executable and arguments, using Windows double quotes around paths with spaces. Use the editor's blocking mode (for example, `$env:EDITOR = 'code.cmd --wait'`) so editing finishes before `bm` saves the result. `VISUAL` takes precedence over `EDITOR`.
- Windows `.cmd`/`.bat` editor launchers are supported with quoted arguments and shell expansion disabled. Arguments or paths containing double quotes, `%`, `!`, or line breaks are rejected for batch launchers; use a native `.exe` editor for those paths. Native editor launches do not use a shell.
- Atomic replacement and no-clobber operations use the corresponding Windows filesystem APIs. Long-path limits and filesystem capabilities still apply.

---

## Security & robustness

- **Atomic writes**: modifications write to a same-directory temp file, flush it, and use `os.replace`; directory fsync is best-effort where supported
- **Conflict-aware mutations**: read/modify/write operations compare byte snapshots and raise a conflict instead of overwriting changes observed before the final check; portable stdlib APIs cannot close the narrow external-writer check/replace race, and multi-file operations may retain safe partial progress before a later conflict, so this is not a transaction guarantee
- **Crash durability**: files are flushed and fsynced before replacement; containing-directory fsync is best-effort because platform/filesystem support varies. Atomic visibility and crash durability are separate guarantees.
- **No-clobber creation/moves**: no-force add/import publish a fully written temp file with an atomic hard link, and no-force moves use link-then-unlink. Existing destinations are never overwritten; a crash during creation can leave a harmless temporary hard-link name, while a crash during a move can leave both source and destination names. Both states are safe to reconcile on retry. Filesystems without hard-link support fail rather than falling back to replacement.
- **Path safety**: `..`, absolute paths, and paths that resolve outside the store are rejected. As with the optimistic content checks above, portable path validation cannot eliminate every race against a malicious process that concurrently rewrites filesystem links.
- **No network by default**: `bm` never fetches content (future hooks can)
- **Git**: pushes only if an upstream is configured and Git commands are bounded/non-interactive

---

## Development

```bash
# syntax check
python3 -m compileall src

# lint and format checks (install the dev extra first when needed)
ruff check .
ruff format --check .

# test suite
python3 -m pytest -q
```

For a reproducible development environment with uv:

```bash
uv sync --locked --extra dev --extra build --python 3.15
uv run --locked --extra dev ruff check .
uv run --locked --extra dev ruff format --check .
uv run --locked --extra dev pytest
uv build
uv run --locked --extra build twine check --strict dist/*
```

The lockfile includes development and build tools; the runtime remains standard-library-only.
The project applies a three-day release-age cooldown to dependency resolution in both local and CI runs.
Keep the pinned Ruff version in `pyproject.toml` and `prek.toml` synchronized when updating it.
CI checks package builds and metadata without publishing. Release publishing remains a separate,
explicitly authorized action.

### Roadmap

The tracked [0.4.0 roadmap](docs/roadmap-0.4.0.md) focuses on lossless backup and
restore, store diagnostics, easier daily capture, cross-platform packaging, and a secure
browser integration. It also records open product and compatibility decisions before they
change public behavior.

---

## License

See [LICENSE](./LICENSE).

MIT. Do what you want; a credit is appreciated.

[tests-badge]: https://github.com/jtabke/bkmrk/actions/workflows/tests.yml/badge.svg?branch=main
[tests-workflow]: https://github.com/jtabke/bkmrk/actions/workflows/tests.yml
