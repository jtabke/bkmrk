# bm — plain‑text bookmarks

[![Tests][tests-badge]][tests-workflow]

A tiny, **stdlib‑only** bookmark manager inspired by the Unix philosophy and `pass`:

- **One text file per bookmark** (`.bm`) with front matter + freeform notes
- **Human‑readable paths** with a short hash to avoid collisions
- **Stable IDs** derived from the URL (rename‑safe)
- **Greppable** store; composable CLI
- **Atomic writes** & path‑safety checks
- **JSON / JSONL** output for pipelines
- **Netscape HTML import/export** for browser interoperability
- **Optional Git sync** for history and cross‑device

> Works on macOS, Linux, WSL, and Windows (PowerShell). No third‑party dependencies.

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

Requires Python >=3.8. Install from PyPI:

```bash
pip install bkmrk
```

Alternatively, using uv to install system-wide as a tool:

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
uv pip install -e '.[dev]'
```

Prefer running straight from the repository? The module entry point works without
installation:

```bash
python -m bm --help
```

On Windows (PowerShell):

```powershell
python -m bm --help
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

File names are **human readable** and end with a **short hash of the URL** to avoid collisions.

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

Default semantics: case‑insensitive substring **AND** across title, url, tags, and body. Each whitespace‑separated word in the query must appear somewhere; quote phrases at the shell level if you need a single token.

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
bm open <ID|path>
```

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

Netscape HTML (for browsers) and JSON exports; Netscape import with folder hierarchies preserved.

```bash
bm export netscape [-t TAG] [--host HOST] [--since ISO|YYYY-MM-DD] [--path PREFIX] > bookmarks.html
bm export json [-t TAG] [--host HOST] [--since ISO|YYYY-MM-DD] [--path PREFIX] [--jsonl] > dump.json
bm import bookmarks.html [-f]
```

### `sync`

If the store is a Git repo, stage/commit and (if upstream exists) push.

```bash
bm sync
```

---

## Filtering & output formats

- `--host` matches the URL host (case‑insensitive, ignores leading `www.`)
- `--path` filters by path prefix (e.g., `--path dev/python` shows only entries under that directory tree)
- `--since` accepts `YYYY-MM-DD` or full ISO timestamps; comparisons are proper datetimes
- `--json` emits a single JSON array; `--jsonl` outputs one JSON object per line (NDJSON)

Common JSON schema fields: `id`, `path`, `title`, `url`, `tags`, `created`, `modified`.

---

## Integration recipes

**fzf launcher**

```bash
bm list --jsonl | fzf --with-nth=2.. | awk '{print $1}' | xargs -r bm open
```

**Open the latest saved from a host**

```bash
bm list --host example.com --jsonl | head -1 | jq -r '.id' | xargs -r bm open
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
bm list --host news.ycombinator.com --jsonl | jq -r '.id' | xargs -n1 bm tag add hn
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

- **Store directory**: set `BOOKMARKS_DIR` or pass `--store` to any command
- **Editor**: `VISUAL` or `EDITOR` (supports commands like `code --wait`)
- **Debug**: set `BM_DEBUG=1` to re-raise unexpected exceptions with a full traceback (otherwise printed as a one-line `bm: <Type>: <msg>` to stderr with exit code 2)
- **Shell completion**: install the `completion` extra (`pip install 'bkmrk[completion]'`) for [argcomplete](https://github.com/kislyuk/argcomplete). Then enable global completion (`activate-global-python-argcomplete`) or wire it per‑shell with `eval "$(register-python-argcomplete bm)"`.

Windows notes:

- Paths avoid reserved names and use atomic replaces; long paths depend on OS settings

---

## Security & robustness

- **Atomic writes**: all modifications write to a temp file then `os.replace` it
- **Path safety**: `..` and absolute paths are rejected; files cannot escape the store
- **No network by default**: `bm` never fetches content (future hooks can)
- **Git**: pushes only if an upstream is configured

---

## Development

```bash
# lint (optional) — stdlib only, so just run the script
python3 -m compileall src

# run tests (if added)
pytest -q
```

### Roadmap / ideas

- `bm reindex` + optional on‑disk index for very large stores
- Markdown/CSV exports
- Simple HTTP UI (`bm serve`) and browser extension hooks
- Optional encryption (GPG or git‑crypt) for private notes

---

## License

See [LICENSE](./LICENSE).

MIT. Do what you want; a credit is appreciated.

[tests-badge]: https://github.com/jtabke/bkmrk/actions/workflows/tests.yml/badge.svg?branch=main
[tests-workflow]: https://github.com/jtabke/bkmrk/actions/workflows/tests.yml
