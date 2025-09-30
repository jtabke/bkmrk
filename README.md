# bm — plain‑text bookmarks

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
  - [`export` and `import`](#export-and-import)
  - [`sync`](#sync)

- [Filtering & output formats](#filtering--output-formats)
- [Integration recipes](#integration-recipes)
- [Configuration](#configuration)
- [Security & robustness](#security--robustness)
- [Migration notes](#migration-notes)
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

For the latest development version, clone and install locally:

```bash
git clone https://github.com/jtabke/bkmrk
cd bkmrk
python -m pip install .
```

Development workflows can pull in the optional extras declared in `pyproject.toml`:

```bash
python -m pip install -e '.[dev]'
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
bm import netscape ~/Downloads/bookmarks.html

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

Full‑text search across title, url, tags, and body.

```bash
bm search <query> [--path PREFIX] [--json|--jsonl]
```

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

### `export` and `import`

Netscape HTML (for browsers) and JSON exports; Netscape import with folder hierarchies preserved.

```bash
bm export netscape [--host HOST] [--since ISO|YYYY-MM-DD] > bookmarks.html
bm export json > dump.json
bm import netscape bookmarks.html [-f]
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

Windows notes:

- Paths avoid reserved names and use atomic replaces; long paths depend on OS settings

---

## Security & robustness

- **Atomic writes**: all modifications write to a temp file then `os.replace` it
- **Path safety**: `..` and absolute paths are rejected; files cannot escape the store
- **No network by default**: `bm` never fetches content (future hooks can)
- **Git**: pushes only if an upstream is configured

---

## Migration notes

From older versions where IDs depended on path: IDs are now **URL‑only** (stable across renames). Old files remain valid; tags as comma strings are still read and normalized to lists on save.

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

## [ License ](./LICENSE)

MIT. Do what you want; a credit is appreciated.
