# Store diagnostics and manual recovery

```sh
bm doctor --store /path/to/bookmarks
bm doctor --store /path/to/bookmarks --json
```

Doctor is read-only. It never creates a missing store, repairs bookmarks, removes files,
refreshes the Git index, edits configuration, invokes an editor/browser, or contacts a
remote. Git-enabled stores use only bounded, hardened local `rev-parse` and
`check-ref-format` commands. Filesystem reads may update access times as usual.

Exit status is 0 when no findings exist and 1 when any findings exist, including warnings
or a missing store. Argument errors retain status 2. Warnings are reasons to inspect, not
proof of data loss or permission to delete anything.

## Report contract

`--json` writes one JSON object to stdout, without human text or diagnostic chatter:

```json
{
  "store": "/path/to/bookmarks",
  "checked": 1,
  "ok": false,
  "findings": [
    {
      "code": "missing_url",
      "severity": "error",
      "paths": ["dev/example.bm"],
      "message": "Bookmark has no URL.",
      "hint": "Restore the URL from a trusted copy or edit this bookmark manually."
    }
  ]
}
```

`checked` counts regular bookmark candidates attempted, including those that fail content
or path validation. Symlinks and other non-regular files are not counted as checked.
Paths within findings are sorted; findings are sorted by paths and then code. Single-file
findings and grouped duplicate findings use the same `paths` array. Git metadata paths
are shown logically under `.git`, including for linked worktrees.

Messages and hints are human-facing; use `code`, `severity`, and `paths` in scripts.
A finding means the store needs attention, not that every command on it will fail.

## What to check and how to recover

Before changing damaged files, stop concurrent writers and preserve a copy. A complete
`bm export -o backup.json` preserves exact UTF-8 bookmark content, but refuses unreadable,
non-UTF-8, or unsafe files. If export fails, preserve those files separately using trusted
filesystem tools; do not overwrite your last known-good backup.

| Finding code | Meaning and manual next step |
|---|---|
| `missing_store` | Check `--store` and `BOOKMARKS_DIR`. Run `bm init` only if a new store is intended. |
| `invalid_front_matter` | Missing/incomplete `---` delimiters, malformed header lines, or repeated keys. Preserve notes and unknown metadata while correcting the header. Legacy URL-only files remain readable by existing commands but are flagged as lacking front matter. |
| `invalid_utf8` | Preserve original bytes and correct the encoding manually before attempting backup/restore. |
| `missing_url` | Recover the URL from a trusted copy or edit the bookmark manually. |
| `unsupported_url_scheme` | Scheme is absent or outside HTTP(S), FTP(S), and mailto. Check the value; do not open an untrusted scheme. |
| `invalid_url` | URL parsing failed, a web/FTP host or mailto address is absent, or a port is invalid. Correct the saved value without fetching it. |
| `duplicate_id` | Multiple usable URLs have the same URL-derived stable ID. Compare paths, metadata, and notes before deciding which file to keep. |
| `duplicate_url` | URLs are equivalent under the existing dedupe normalization rules. Review `bm dedupe --dry-run`; merging is a separate, explicit action. |
| `unsafe_or_unreadable_path` | A bookmark path is unsafe, non-regular, a symlink, or cannot be read. Inspect permissions/links manually. No symlink targets are intentionally inspected. |
| `symlink_directory` | A directory link was skipped. Inspect its target manually; doctor does not descend into it. |
| `unreadable_directory` | A directory could not be enumerated. Fix access only if authorized, then rerun doctor. |
| `temporary_file` | A `.tmp` or `.edit` file may be left by an interrupted write/editor. First check for an active writer, then compare its content with the bookmark. |
| `temporary_hardlink` | A temporary file has multiple hard links, possibly from interrupted atomic publication. Check for active writers and compare files before removing a name. |
| `possible_interrupted_move` | Multiple bookmark paths refer to the same hard-linked file. It may be an interrupted no-clobber move or an intentional hard link. Inspect both names; do not blindly delete either. |
| `git_symlink` | The store's `.git` marker is a symlink and was not inspected. Check it manually. |
| `git_problem` | Git discovery/configuration or HEAD checks failed, or Git is unavailable. Inspect metadata manually; doctor does not reinitialize the repository. |
| `git_lock` | A common Git lock file is present. It may belong to an active process. Confirm ownership before considering removal. |

Duplicate stable IDs and normalized URLs are separate diagnostics: two identical URLs
produce both; HTTP/HTTPS or fragment variants can share a normalized URL without sharing
a stable ID. Unknown metadata and properly formed block scalars are accepted. Doctor is
not a general YAML validator: `.bm` retains its existing small front-matter grammar.

Git checks accept a healthy unborn repository. They check local discovery (including
configuration parsing), HEAD's symbolic-ref/hash shape, and the presence of common
`index`, `HEAD`, `config`, `packed-refs`, and `shallow` lock files. They do not prove object
integrity, ref reachability, remote availability, or clean synchronization state. They do
not run `status`, `fsck`, hooks, credential helpers, or configured fsmonitor commands.

## Limits

Doctor is not a filesystem snapshot or lock. A concurrent writer can change files between
checks, and portable path checks cannot eliminate races against malicious link changes.
Stop writers and rerun the command to investigate inconsistent results. A clean report
means only that these checks found no problems, not that every possible corruption or
interrupted operation has been ruled out. There is deliberately no `--repair` option.
