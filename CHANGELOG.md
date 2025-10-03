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
