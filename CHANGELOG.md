## 0.4.0 (2026-11-20)

### Breaking change

- Python 3.12 or newer is required. If you use Python 3.8–3.11, stay on `bkmrk==0.3.1`.

### Features

- `bm export` now backs up exact bookmark paths and UTF-8 file contents. Preview a restore with
  `bm import backup.json --dry-run`. Explicit JSON/JSONL and browser HTML exports are unchanged.
- `bm add --batch` saves one URL per line. Rerun with `--resume` to skip matching entries
  without overwriting bookmarks. Single and batch capture warn about possible duplicates.
- Capture a URL with `bm add --clipboard`, copy one with `bm copy`, or select a bookmark
  interactively with `bm open --search QUERY` and fzf.
- `bm doctor` reports store problems and recovery hints without modifying bookmarks.
  Use `bm --version` to check the installed version.

### Fixes and performance

- Lookups prefer the literal listed path and reject ambiguous IDs. Use `--exact-path` with
  `edit`, `tag`, `mv`, or `rm` to disable fallback when changing a bookmark.
- If a valid edit cannot be saved, `bm` reports where to find the recovery draft. No-op tag
  changes leave the file and its timestamp untouched.
- Improved Windows path handling, editor launching, and invalid filename diagnostics.
- The CLI starts faster. Batch capture scans existing headers once, and path-filtered exports
  skip unrelated bookmarks.

## 0.3.1 (2026-08-23)

### Fix

- parse Netscape bookmarks structurally
- tighten persistence and input contracts
- bound external commands and escape exports
- harden bookmark persistence invariants

### Refactor

- remove transitional command compatibility
- split query and interchange concerns
- centralize store operations and application errors

## 0.3.0 (2026-05-03)

### Feat

- **commands**: security hardening, perf, search modes, progress, robustness
- **cli**: unified filters, search modes, exception handler, completion
- **security**: tighten path containment in id_to_path

### Fix

- harden URL slug normalization
- return bool from since filter predicate
- **io**: block-scalar correctness, faster meta-only reads, refuse symlink dest

## 0.2.2 (2026-03-08)

### Fix

- simplify import to accept file directly
- sort JSON export rows by path for deterministic output
- flatten host into filename instead of creating directory

### Refactor

- reuse existing helpers to reduce duplication

### Perf

- optimize hot paths and harden temp file handling

## 0.2.1 (2025-10-03)

### Fix

- surface git failures during sync
- restore block scalars in front matter
- preserve merged modified timestamp

## 0.2.0 (2025-09-30)

### Feat

- add dedupe command

### Fix

- **ci**: correct publish workflow permissions

## 0.1.0 (2025-09-27)

### Feat

- add build deps
- add commitizen for automated semantic versioning and changelog generation
- add path-scoped filters and directory listing
- implement import/export folder hierarchies from Netscape HTML

### Fix

- normalize_slug strips dangling dashes from path segments
- allow attributes in H3 tags for folder parsing in Netscape HTML importer' manually

### Refactor

- organize code into src/
- normalize_slug removes unmatched characters instead of replacing with dash
