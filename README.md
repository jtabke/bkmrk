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
  - [`copy`](#copy)
  - [`edit`, `rm`, `mv`](#edit-rm-mv)
  - [`tags` and `tag add|rm`](#tags-and-tag-addrm)
  - [`dirs`](#dirs)
  - [`dedupe`](#dedupe)
  - [`doctor`](#doctor)
  - [`export` and `import`](#export-and-import)
  - [`sync`](#sync)

- [Filtering & output formats](#filtering--output-formats)
- [Integration recipes](docs/integrations.md)
- [Configuration](#configuration)
- [Security & robustness](#security--robustness)
- [Guides](#guides)
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

For source installs, development environments, and running directly from a checkout,
see [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Quickstart

Watch the [short terminal walkthrough](docs/terminal-demo.md) for installation from a
working-tree wheel, adding/searching bookmarks, reading a `.bm` file, and inspecting a Git diff.

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

# choose a matching bookmark to open (optional fzf required)
bm open --search kernel

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

The `.git` directory namespace is reserved at every depth (case-insensitive). New bookmark
paths cannot target it. Existing `.bm` files there are reported by `doctor` and block
backup export rather than being silently omitted; preserve and relocate them manually.

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

- `tags` accepts ordinary lists such as `[read, "with,comma"]`.
  In these legacy lists, backslashes are always literal, even inside single or double
  quotes: `tags: ["C:\new folder"]` retains the backslash rather than introducing a newline.
  When tags need escape sequences, writers use an explicit `json:` prefix, for example
  `tags: json:["say\"hello", "C:\\new folder"]`. Only this marked form interprets JSON
  escapes. Ordinary lists remain readable, and rewrites preserve their tag values.
- Parsed rewrites retain non-empty unknown fields, not original formatting or comments.
  Complete backups preserve exact UTF-8 content. See the
  [format and CLI compatibility policy](docs/compatibility.md) for operation-specific limits.

### IDs

Each bookmark has a **stable ID** derived from its URL (BLAKE2b short hash). The ID does **not** change if you rename/move the file. You can use either the ID **or** a path‑like slug with commands.

Several files can share an ID, including separate bookmarks with the same URL. ID lookup
reports ambiguity instead of choosing the first file; use a complete path to select one.
Existing exact-path and partial-name lookup precedence is unchanged.

---

## CLI usage

Run `bm --help` or `bm <command> --help` for command details.
`--store` works before or after the command (and after an export format); if repeated,
the last value wins. For example, `bm --store ./bookmarks list` and
`bm list --store ./bookmarks` select the same store.

### `init`

Create a store; optional `--git` initializes a Git repo.

```bash
bm init --git
```

### `add`

Add a bookmark. `--edit` opens your `$EDITOR` with a pre‑filled template.
Use `-` explicitly to read one URL from stdin; empty input, multiple URLs/lines, and
embedded whitespace are rejected without writing a bookmark.

```bash
printf '%s\n' 'https://example.com/docs' | bm add - --name "Docs" --tags dev
```

Stdin is never read unless requested. Plain URL and stdin capture do not access the clipboard
or fetch a title. To explicitly capture a copied URL instead:

```bash
bm add --clipboard --name "Docs" --tags dev
bm add --clipboard --edit              # Review title, tags, and notes before saving
```

`--clipboard` replaces the URL argument; it cannot be combined with a URL or `-`. It accepts
one structurally valid `http`, `https`, `ftp`, `ftps`, or `mailto` URL, trimming surrounding
whitespace. Empty text, batches, embedded whitespace, and unsupported/malformed URLs are
rejected before writing. No title is fetched. See [clipboard setup](docs/setup.md#clipboard-setup).

```bash
bm add (<url>|--clipboard) [-n TITLE] [-t tag1,tag2] [-d NOTES] [-p dir1/dir2] [--id SLUG] [--edit] [-f]
```

Prints the stable ID on success. If an equivalent normalized URL exists at another path,
prints an advisory warning on stderr with the existing paths and show/open guidance.
Duplicates remain allowed: no prompt, merge, or change to stdout/exit status. The existing
bookmarks are left unchanged. Matching uses the same heuristic as `dedupe` (including
ignoring HTTP/HTTPS differences and fragments), not a guarantee of identical content.
Same-path collisions still fail unless `--force` explicitly replaces that destination.
The best-effort metadata scan is not a uniqueness lock; concurrent adds can race.

### `list`

List bookmarks (newest first).

```bash
bm list [--host HOST] [--since ISO|YYYY-MM-DD] [-t TAG] [--path PREFIX] [--json|--jsonl]
```

### `search`

Default semantics: case-insensitive substring **AND** across title, URL, tags, and body.
The query is split on whitespace, and each term must appear somewhere in the combined
fields. `bm search python async` and `bm search "python async"` are equivalent. Options can
also appear between terms: `bm search python --tag dev async`. Unknown options remain
errors; use `--` before literal terms starting with `-`. Use `--regex` for phrase or pattern matching, passing
one pattern argument (quote it when it contains spaces).

```bash
bm search <terms...> [-t TAG] [--host HOST] [--since ISO|YYYY-MM-DD] [--path PREFIX] [--regex] [--field FIELD] [--json|--jsonl]
```

- `--regex` treats the query as a Python regex (case‑insensitive).
- `--field {title,url,tags,body}` restricts the scope; repeat to search multiple fields.
- Exits **0** when at least one match is printed, **1** when there are no matches (so `bm search foo && open …` works as expected).

### `show` and `open`

Display metadata/notes, open a known bookmark directly, or choose one interactively:

```bash
bm show <ID|path>
bm open <ID|path>                       # Direct lookup; no fzf or terminal required
bm open                                # Choose a bookmark, then open it
bm open --search "python async"         # Search first, then choose a result
bm open --search python --tag dev --copy # Choose a matching URL to copy instead
bm open --tag dev --host example.com
bm open --path dev/python --since 2025-01-01 --print url
bm open dev/docs --print url            # Print a known URL without opening or selecting
```

An ID/path always uses existing bookmark resolution, never full-text search. A missing or
ambiguous bookmark is an error, not a fallback to selection. Use `--search QUERY` explicitly;
quote multiple terms. An ID/path cannot be combined with `--search` or selection filters.
`bm search` remains the noninteractive command for printing results and JSON/JSONL pipelines.

Without an ID/path, `open` requires terminals on **stdin and stderr** and an optional system
installation of `fzf`. Redirected stdin or stderr fails with guidance (exit 2), without
starting fzf. Stdout can be redirected for `--print`. Direct ID/path opening, copying, and
printing need neither a terminal nor fzf.

Selection supports the same tag/host/path/since filters as `list`. `--search` uses the same
case-insensitive substring-AND matching as ordinary `search`, including notes. The query and
filters narrow candidates before fzf starts; typing in fzf then fuzzy-matches visible
path/title/URL, not hidden notes. With no query, candidates are recent-first. Even a single
match requires confirmation; no first result is opened automatically.

The default action opens the chosen URL in your default browser and prints it to stdout.
`--copy` copies its URL instead; `--print id|url` prints only that field. These options are
mutually exclusive, work for both direct lookup and selection, and never open a browser.
Cancellation opens and copies nothing. Missing fzf gives installation guidance (exit 2).
No initial matches exit 1 without launching it; fzf no-match/cancel exits 1/130 quietly.
Other fzf failures or invalid selections exit 2. Notices and errors do not pollute stdout.

Opening accepts `http`, `https`, `ftp`, `ftps`, and `mailto` schemes by default. Use
`bm open <ID|path> --allow-scheme` only when intentionally opening another scheme.
`--allow-scheme` is rejected for interactive selection, copying, and printing.

Interactive opening ignores `FZF_DEFAULT_OPTS` and `FZF_DEFAULT_OPTS_FILE` so user-defined
shell bindings/previews cannot run unexpectedly or change the selection protocol. Display
control characters are sanitized, and no shell command is constructed from bookmark data.
See `bm open --help`. The unreleased `pick` command has been folded into `open`; no alias is
retained.

### `copy`

Copy a stored URL without opening a browser or changing the bookmark:

```bash
bm copy <ID|path>
bm open --search python --copy         # Search and choose when you don't know the path
```

Success leaves stdout empty and prints `Copied URL to clipboard.` on stderr. Copy uses the
stored URL as text (even for a nonstandard scheme), not notes or metadata; it does not execute
or open it. Missing/ambiguous entries and missing URLs fail without touching the clipboard.
Clipboard-helper failures exit 2; see [clipboard setup](docs/setup.md#clipboard-setup).

### `edit`, `rm`, `mv`

```bash
bm edit <ID|path>   # bumps modified timestamp
bm rm <ID|path>
bm mv <SRC> <DST> [-f]
```

`rm` permanently deletes the bookmark file; there is no trash or built-in undo. Recovery
requires an existing Git snapshot or [complete backup](docs/backup-format.md).

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

Dedupe is not a whole-store transaction. A late conflict or I/O failure can leave earlier
merges/deletions applied; the error makes this explicit and retains the failure status.
Inspect the store and rerun `bm dedupe --dry-run` before retrying. Do not assume that a
failed command rolled back previous groups or completed every planned deletion.

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

### `doctor`

Inspect the store without changing bookmarks or Git configuration:

```bash
bm doctor
bm doctor --store ./bookmarks --json
```

Reports malformed/incomplete front matter, invalid UTF-8, missing/malformed/unsupported
URLs, duplicate stable IDs and normalized URLs separately, unsafe or unreadable paths,
temporary files and possible interrupted hard-link moves. Basic local Git checks cover
repository discovery/configuration errors, HEAD shape, and common lock files. An unborn
repository is valid; this is not a full Git object-integrity audit.

Each finding includes a manual recovery hint. No automatic repair, network requests,
browser/editor launches, or Git index refreshes occur. Exit status is **0** with no
findings and **1** with any findings (including warnings or a missing store). JSON emits
one report containing `store`, `checked`, `ok`, and `findings`; each finding has `code`,
`severity`, `paths`, `message`, and `hint`. See [diagnostics and recovery](docs/store-diagnostics.md)
for scope and recovery steps.

### `export` and `import`

**Plain export is a complete, lossless bookmark backup.** Import recognizes backups and
Netscape browser HTML automatically. See the
[browser migration guide](docs/browser-migration.md) for Chrome, Firefox, Safari, and Edge
export recipes and a reproducible import-preview example.

```bash
bm export -o bookmarks.json             # Safely create a new backup file
bm export > bookmarks.json             # Same JSON to stdout; shell redirection can overwrite
bm import bookmarks.json --dry-run     # Validate and preview without filesystem changes
bm import bookmarks.json               # Restore missing bookmarks; leave identical ones alone
bm import bookmarks.json --force       # Also replace conflicting paths, never delete unrelated files
```

`-o` / `--output` works with every format, before or after the format name:
`bm export json -o metadata.json` or `bm export -o bookmarks.html netscape`.
It refuses existing output files and publishes the completed export atomically.
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
Netscape import preserves folder hierarchies and retains its best-effort skip behavior.
Its dry-run reports added/replaced/skipped-existing/invalid counts without changing files:

```text
Would add 42, replace 0, skip existing 8; invalid 2.
No files written.
```

Invalid counts cover missing URL attributes, malformed URLs, unsupported schemes, and
unsafe folder paths. Repeated destinations are counted as skipped-existing by default;
with `--force`, later records replace earlier ones. Counts represent record actions, not
unique files. Normal browser import still prints `import ok`.

Use `-` to import a backup or browser HTML from stdin, including in dry-run mode:

```bash
bm export | bm --store ./restored import -
cat bookmarks.html | bm import - --dry-run
```

Stdin backup detection uses the document's JSON object/array shape; invalid backup JSON
is rejected before mutation. Input files retain extension-based JSON detection as well.
Empty input is rejected. Standard input is never read implicitly.

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

## Configuration

- **Store directory**: set `BOOKMARKS_DIR`, or use `bm --store PATH <command>`.
- **Editor**: set `VISUAL` or `EDITOR` (supports commands like `code --wait`).
- **Debug**: set `BM_DEBUG=1` to re-raise unexpected exceptions with a full traceback
  (otherwise printed as a one-line `bm: <Type>: <msg>` to stderr with exit code 2).

See [setup and platform notes](docs/setup.md) for clipboard helpers, optional shell
completion, fzf selection, and Windows editor/path behavior.

---

## Security & robustness

- **Atomic writes**: modifications write to a same-directory temp file, flush it, and use `os.replace`; directory fsync is best-effort where supported
- **Conflict-aware mutations**: read/modify/write operations compare byte snapshots and raise a conflict instead of overwriting changes observed before the final check; portable stdlib APIs cannot close the narrow external-writer check/replace race, and multi-file operations may retain safe partial progress before a later conflict, so this is not a transaction guarantee
- **Crash durability**: files are flushed and fsynced before replacement; containing-directory fsync is best-effort because platform/filesystem support varies. Atomic visibility and crash durability are separate guarantees.
- **No-clobber creation/moves**: no-force add/import publish a fully written temp file with an atomic hard link, and no-force moves use link-then-unlink. Existing destinations are never overwritten; a crash during creation can leave a harmless temporary hard-link name, while a crash during a move can leave both source and destination names. Both states are safe to reconcile on retry. Filesystems without hard-link support fail rather than falling back to replacement.
- **Path safety**: `..`, absolute paths, and paths that resolve outside the store are rejected. As with the optimistic content checks above, portable path validation cannot eliminate every race against a malicious process that concurrently rewrites filesystem links.
- **URL policies**: capture preserves user-entered URLs; browser import rejects malformed or
  unsupported URLs. Exact backup restore checks supported schemes but preserves malformed
  hosts/ports for recovery; `doctor` diagnoses those structural problems. Opening remains
  scheme-gated, with `--allow-scheme` as an explicit override. URL inspection does not
  normalize saved URLs, stable IDs, or dedupe keys.
- **No network by default**: `bm` never fetches content (future hooks can)
- **Git**: pushes only if an upstream is configured and Git commands are bounded/non-interactive

---

## Guides

- [Setup and platform notes](docs/setup.md): clipboard, completion, fzf, and Windows.
- [Integration recipes](docs/integrations.md): launchers, bulk tagging, and synchronization.
- [Browser migration](docs/browser-migration.md): export from a browser and preview an import.
- [Backup and recovery](docs/backup-format.md): exact-content backups and restore guarantees.
- [Store diagnostics](docs/store-diagnostics.md): read-only health checks and recovery advice.
- [Compatibility policy](docs/compatibility.md): file preservation and CLI contracts.
- [Terminal walkthrough](docs/terminal-demo.md): a reproducible disposable-store demo.

---

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, checks, module boundaries,
and safe disposable-store experiments. File preservation and CLI evolution follow the
[compatibility policy](docs/compatibility.md).

Browser integration is deferred to avoid maintaining an extension; browser opening and
HTML import/export remain supported.

---

## License

See [LICENSE](./LICENSE).

MIT. Do what you want; a credit is appreciated.

[tests-badge]: https://github.com/jtabke/bkmrk/actions/workflows/tests.yml/badge.svg?branch=main
[tests-workflow]: https://github.com/jtabke/bkmrk/actions/workflows/tests.yml
