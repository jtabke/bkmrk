# Lossless backup format

## Everyday use

```sh
bm export -o bookmarks.json
bm import bookmarks.json --dry-run
bm import bookmarks.json
```

Use `bm --store /path/to/store ...` to select a store explicitly. Plain `export` writes
the same document to stdout, with no status text. `-o` publishes a completed file using
an atomic no-clobber create; it never replaces an existing output file. The parent
directory must exist. Shell redirection is controlled by the shell and can overwrite
an existing file before `bm` runs; prefer `-o` for backups.

`export netscape` and `export json` / `--jsonl` retain their existing formats and filters.
They are interchange/pipeline exports, not lossless backups. Complete backups have no
filter options and need no additional top-level commands.

## Version 1 contract

A backup is a UTF-8 JSON object:

```json
{
  "format": "bm-backup",
  "version": 1,
  "entries": [
    {
      "path": "dev/example.bm",
      "content": "---\nurl: https://example.com\ntitle: Example\ncustom: retained\n---\nNotes\n"
    }
  ]
}
```

- The envelope contains exactly `format`, `version`, and `entries`.
- `format` is the string `bm-backup`; `version` is the integer `1`.
- `entries` is an array. Each record contains exactly the string fields `path` and
  `content`. Duplicate JSON object keys are invalid.
- `path` is the exact store-relative filename **including `.bm`**, with `/` separators.
  Restore never slugifies it or regenerates a stable ID. Absolute/drive-qualified paths,
  backslashes, empty or all-dot components, NUL bytes, and `.git` components are rejected.
  Windows also rejects unsupported filenames and device names. Unsafe names are rejected,
  not renamed or skipped.
- `content` is the entire UTF-8 file, not a parsed-and-reconstructed metadata object.
  Encoding this string as UTF-8 reproduces the original bytes, including CRLF/LF line
  endings, comments, metadata spelling/order, unknown fields, and body whitespace.
- Export orders entries by path and emits no generation timestamp. An unchanged store
  produces the same document. Restore does not depend on record order.
- Unsupported versions and unknown fields are rejected rather than guessed at. Future
  incompatible contracts require a new version.

The archive covers `.bm` files only. Git data, other store files, empty directories,
permissions, filesystem timestamps, and hard-link identity are outside the contract.
Export excludes `.git` directories and refuses symlink files/directories elsewhere in
the bookmark tree. Unreadable, non-regular, or non-UTF-8 bookmarks fail the export; they
are never silently omitted. Export preserves content without requiring it to be a valid
bookmark. A damaged bookmark can therefore be backed up, but must be corrected before
restore if its URL is missing or unsupported.

## Import safety and output

Import detects a JSON backup from its content (or a `.json` / `.jsonl` extension) and
validates the entire document and all destination paths before any writes. Ordinary
pipeline JSON is rejected with guidance to create a complete backup. Browser HTML
continues to use the existing best-effort import rules.

For a backup, preflight rejects malformed records, duplicate or overlapping destinations,
symlink path components, escapes from the store, non-file destinations, and URLs whose
scheme is missing or outside `http`, `https`, `ftp`, `ftps`, and `mailto`. Validation does
not fetch URLs or launch external programs.

| Destination | Default | `--force` |
|---|---|---|
| Missing | Add | Add |
| Identical bytes | Leave unchanged | Leave unchanged |
| Different bytes | Conflict; block all writes | Replace |
| Unrelated to backup | Leave untouched | Leave untouched |

A preview might print:

```text
Would add 42, replace 0, leave unchanged 8; conflicts 2.
No files written.
```

Conflicting paths are listed on stderr in sorted order, with guidance to use `--force`.
Conflicts and invalid backups exit 1, including in dry-run mode. A conflict-free dry-run
exits 0 and creates no directories or files. A successful restore prints:

```text
Added 42, replaced 2, left unchanged 8.
```

A repeated import is safe: identical files are not rewritten. `--force` is path-based
replacement, **not** deletion, URL deduplication, or whole-store synchronization.

## Concurrency and interrupted restores

Preflight is not a lock or transaction. New bookmarks use atomic no-clobber publication;
replacement checks the bytes observed during preflight before atomic replacement. An
external writer can still race the final optimistic check, and path checks cannot defeat
every malicious concurrent filesystem mutation. Export likewise reads files sequentially,
not as a point-in-time snapshot; stop other writers if a coherent collection snapshot is
required.

If writing fails, import stops with an error explaining that earlier files may already
have been written. It does not roll back or delete files. Inspect the reported failure,
resolve it, and retry: completed identical files will be left alone. Do not blindly add
`--force` after a concurrent-edit conflict; first inspect the changed destination.

The existing same-directory temporary-file, fsync, hard-link create, and atomic replacement
safeguards apply. Filesystems without hard-link support fail safely instead of falling
back to an operation that can overwrite another writer's file.
