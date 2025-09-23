# bm — plain‑text, pass‑style bookmarks

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

Install from source (requires Python >=3.8):

```bash
git clone https://github.com/jtabke/bkmrk
cd bm
pip install .
```

Or for development (editable install):

```bash
pip install -e .
```

This installs the `bm` command globally.

Alternatively, run directly without installing:

```bash
python3 bm.py --help
```

On Windows (PowerShell):

```powershell
python bm.py --help
```

---

## Quickstart

```bash
# initialize a new store (optionally a git repo)
./bm.py init --git

# add a bookmark
./bm.py add https://example.com -n "Example" -t ref,demo -d "Short note"

# list newest bookmarks (ID, path, title, URL)
./bm.py list

# search across title/url/tags/body
./bm.py search kernel

# open the first result
ID=$(./bm.py search kernel --jsonl | head -1 | jq -r '.id')
./bm.py open "$ID"

# export for browsers (Netscape HTML)
./bm.py export netscape > bookmarks.html
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

Run `./bm.py --help` or `./bm.py <command> --help` for command details.

### `init`

Create a store; optional `--git` initializes a Git repo.

```bash
bm.py init --git
```

### `add`

Add a bookmark. `--edit` opens your `$EDITOR` with a pre‑filled template.

```bash
bm.py add <url> [-n TITLE] [-t tag1,tag2] [-d NOTES] [-p dir1/dir2] [--id SLUG] [--edit] [-f]
```

Prints the stable ID on success.

### `list`

List bookmarks (newest first).

```bash
bm.py list [--host HOST] [--since ISO|YYYY-MM-DD] [-t TAG] [--json|--jsonl]
```

### `search`

Full‑text search across title, url, tags, and body.

```bash
bm.py search <query> [--json|--jsonl]
```

### `show` and `open`

Display metadata/notes or open the URL in your default browser:

```bash
bm.py show <ID|path>
bm.py open <ID|path>
```

### `edit`, `rm`, `mv`

```bash
bm.py edit <ID|path>   # bumps modified timestamp
bm.py rm <ID|path>
bm.py mv <SRC> <DST> [-f]
```

### `tags` and `tag add|rm`

List discovered tags (from folder segments and header tags), or mutate tags without opening an editor.

```bash
bm.py tags
bm.py tag add <ID|path> tag1 tag2
bm.py tag rm  <ID|path> tag1
```

### `export` and `import`

Netscape HTML (for browsers) and JSON exports; Netscape import with folder hierarchies preserved.

```bash
bm.py export netscape [--host HOST] [--since ISO|YYYY-MM-DD] > bookmarks.html
bm.py export json > dump.json
bm.py import netscape bookmarks.html [-f]
```

### `sync`

If the store is a Git repo, stage/commit and (if upstream exists) push.

```bash
bm.py sync
```

---

## Filtering & output formats

- `--host` matches the URL host (case‑insensitive, ignores leading `www.`)
- `--since` accepts `YYYY-MM-DD` or full ISO timestamps; comparisons are proper datetimes
- `--json` emits a single JSON array; `--jsonl` outputs one JSON object per line (NDJSON)

Common JSON schema fields: `id`, `path`, `title`, `url`, `tags`, `created`, `modified`.

---

## Integration recipes

**fzf launcher**

```bash
bm.py list --jsonl | fzf --with-nth=2.. | awk '{print $1}' | xargs -r bm.py open
```

**Open the latest saved from a host**

```bash
bm.py list --host example.com --jsonl | head -1 | jq -r '.id' | xargs -r bm.py open
```

**Bulk tag HN links**

```bash
bm.py list --host news.ycombinator.com --jsonl | jq -r '.id' | xargs -n1 bm.py tag add hn
```

**Export → browser import**

```bash
bm.py export netscape > ~/Desktop/bookmarks.html
# Import that file in your browser’s bookmarks manager
```

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
python3 -m py_compile bm.py

# run tests (if added)
pytest -q
```

### Roadmap / ideas

- `bm dedupe` (merge by normalized URL, union tags)
- `bm reindex` + optional on‑disk index for very large stores
- Markdown/CSV exports
- Simple HTTP UI (`bm serve`) and browser extension hooks
- Optional encryption (GPG or git‑crypt) for private notes

---

## [ License ](./LICENSE)

MIT. Do what you want; a credit is appreciated.
