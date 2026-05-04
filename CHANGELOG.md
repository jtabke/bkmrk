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
