# Bookmark and CLI compatibility policy

The readable `.bm` file is the durable interface. The core remains standard-library-only;
normal use does not require a database, index, hosted account, or browser extension.
This policy describes the existing format and CLI compatibility requirements,
not a promise that every command preserves the original bytes.

## Files and identities

- One UTF-8 `.bm` file holds front matter delimited by lines containing `---`, followed by
  freeform body notes. This is a small front-matter dialect, **not general YAML**. Nested
  objects, YAML aliases, and arbitrary YAML types are not supported.
- Standard keys are `url`, `title`, `tags`, `created`, and `modified`. Other keys are parsed
  as text (including supported multiline block scalars). Keys are normalized to lowercase;
  do not rely on case distinctions, comments, duplicate keys, or header order surviving
  a parsed rewrite. Empty metadata values may be omitted when rendered.
- Legacy `added`/`updated` timestamps map to `created`/`modified` when the canonical key
  is absent. Tags accept comma-separated text, literal bracketed lists, and explicitly
  escaped `json:[...]` string arrays. Unmarked lists do not interpret backslash escapes.
- Body notes are not interpreted as executable content or a wiki format. Parsing can
  discard blank lines immediately after front matter; individual mutation commands may
  normalize whitespace. Use complete backup for byte-exact preservation.
- The stable ID is derived from the **stored URL**, not the filename. Moving or renaming
  a file does not change the ID; changing the URL can. IDs are short hashes, not globally
  unique identifiers. Multiple files with the same URL share an ID: use an explicit path
  to disambiguate. Normalized-URL matching does not redefine ID generation.
- Paths remain store-relative. Absolute paths, traversal, escapes, and the `.git` namespace
  are not supported bookmark destinations. Symlink and platform filename safeguards still
  apply; a valid Unix filename is not necessarily portable to Windows.

## Preservation depends on the operation

| Operation | Preservation contract |
|---|---|
| List/search/show/doctor/copy | No bookmark rewrite (`copy` explicitly changes the clipboard) |
| Move | Moves the file without parsing and re-rendering its content |
| Tag changes and parsed edits | Retain parsed non-empty unknown fields and notes, but may reformat content; explicit editor changes are authoritative |
| Add with `--force` | Replaces the destination with the newly supplied entry; not a metadata merge |
| Dedupe | Retains survivor metadata, combines selected standard fields/tags and notes; unknown fields from removed entries are not merged |
| Netscape import/export | Browser interchange, not complete metadata or notes preservation |
| Explicit JSON/JSONL export | Pipeline metadata, not a restorable full backup |
| Plain `export` and backup `import` | Exact paths and UTF-8 file content under the versioned [backup contract](backup-format.md) |

Complete backup preserves unknown fields, comments, whitespace, and line endings exactly.
It covers `.bm` files, not Git history, other files, permissions, or filesystem timestamps.
Restore validates supported URL schemes and safe destinations; a damaged file that can
be archived may require manual correction before restore.

## CLI evolution

Existing command names, explicit pipeline formats, success stdout, and exit behavior must
remain compatible unless fixing a documented defect. New diagnostics belong on stderr,
not in machine-readable stdout. New optional capabilities must not silently fetch URLs,
read the clipboard, start a daemon, or install a browser integration.

`add` permits separate bookmarks with equivalent URLs and warns on stderr after a
successful save. It never silently merges them. Same-destination no-clobber behavior and
explicit `--force` replacement remain unchanged. The advisory scan uses dedupe's existing
normalization (including ignoring HTTP/HTTPS differences, leading `www.`, and fragments);
this is a heuristic, not proof that two URLs serve identical content.

Clipboard capture is explicit (`add --clipboard`) and validates a single supported URL
before saving. Existing literal-URL and stdin capture behavior remains unchanged. `copy`
and `open --copy` copy text without opening it; clipboard contents can outlive bm in system
history managers. `open <ID|path>` retains direct resolution, with no automatic full-text
fallback. Bare `open` or explicit `open --search QUERY` selects interactively using optional
fzf, only with stdin/stderr terminals; stdout may be redirected for printing. Selection
always requires confirmation, even for one match. `--copy` and `--print` are exclusive and
never open a browser. IDs cannot be mixed with queries/filters; `--allow-scheme` remains a
direct-opening-only override. `search` remains noninteractive. The unreleased `pick` command
was removed rather than introducing a compatibility alias; it is absent from release 0.3.1.
See
[clipboard setup and limits](setup.md#clipboard-setup).

A future incompatible persisted format needs an explicit version/migration design,
documented preservation limits, and regression tests for existing files. Backup envelope
versions are independent of the package version; unsupported versions are rejected rather
than guessed at.

`rm` permanently deletes the bookmark file, with no trash or built-in undo. Recovery requires
an existing Git snapshot or complete backup. Changing this behavior requires an explicit
compatibility decision.

## Safety limits

Atomic writes and optimistic snapshot checks reduce accidental data loss; they are not a
whole-store transaction or protection against every malicious concurrent filesystem change.
Stop other writers for a coherent backup, and do not share a writable store with untrusted
users. URL duplicate scans are best-effort and unlocked, can skip damaged/unreadable
entries, and do not guarantee uniqueness under concurrency. They scan existing metadata
on each add; no persistent index is introduced.

Bookmark URLs, titles, and notes are untrusted data. Keep external program launches
explicit. See [security and robustness](../README.md#security--robustness) and
[diagnostics/recovery](store-diagnostics.md) for operational limits.
