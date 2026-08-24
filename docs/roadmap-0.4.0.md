# bm 0.4.0 roadmap

## Status

**Proposed release:** 0.4.0  
**Tracking state:** planning  
**Last updated:** 2026-08-23

Legend:

- [ ] Not started
- [~] In progress
- [x] Complete
- [!] Blocked or requires a decision

## Product direction

`bm` is a portable, file-based bookmark manager for the command line. Version 0.4.0
should make the existing model easier to trust, adopt, and use every day without turning
`bm` into a general notes application or hosted service.

The release should answer two user questions:

1. Can I safely move my bookmark collection into and out of `bm`?
2. Is saving and opening a bookmark easier than using my browser's bookmark manager?

## Constraints

- Keep the runtime stdlib-only.
- Keep one readable `.bm` file per bookmark.
- Preserve unknown front-matter metadata and freeform notes.
- Preserve existing CLI output and exit behavior unless a documented defect requires a change.
- Keep network access explicit, optional, and bounded.
- Keep the filesystem backend concrete; do not add a plugin framework, database, or generic
  repository abstraction.
- Prefer small integrations around the CLI and file format over a permanent daemon.
- Treat the bookmark format as a compatibility contract.

## Completed groundwork

- [x] Persistence, path, conflict, and external-command hardening released in 0.3.1.
- [x] Query, Netscape interchange, and application boundaries separated.
- [x] Atomic no-clobber add, import, and move behavior implemented.
- [x] Structural Netscape HTML parser implemented.
- [x] README usage and safety claims audited and corrected on `main`.
- [x] README now opens with a file-based quickstart using the project repository.

## 0.4.0 release themes

### 1. Portability and recovery — must have

#### Lossless JSON backup and restore

- [ ] Define a versioned JSON backup contract.
- [ ] Preserve path, URL, title, tags, timestamps, body, and unknown metadata.
- [ ] Keep the current pipeline-oriented JSON/JSONL output compatible.
- [ ] Add lossless restore with no-clobber behavior by default.
- [ ] Add `--force` only where replacement semantics are explicit.
- [ ] Add `--dry-run` with deterministic counts and diagnostics.
- [ ] Detect malformed records, duplicate destinations, unsafe paths, and unsupported URL
  schemes before mutation begins.
- [ ] Add JSON → store → JSON round-trip tests, including unknown metadata and notes.
- [ ] Document the difference between pipeline export and a complete backup.

**Decision required:** choose between dedicated `bm backup` / `bm restore` commands and an
explicit full-fidelity mode under `bm export` / `bm import`. Do not silently change the
existing JSON schema.

#### Store diagnostics

- [ ] Add a read-only `bm doctor` command.
- [ ] Report invalid or incomplete front matter.
- [ ] Report missing or unsupported URLs.
- [ ] Report duplicate stable IDs and normalized URLs separately.
- [ ] Report temporary hard-link files and possible interrupted moves.
- [ ] Report paths that cannot be resolved safely.
- [ ] Report relevant Git repository problems without changing Git configuration.
- [ ] Support human-readable and JSON output.
- [ ] Document manual recovery steps.

Automated repair is not part of the first implementation. Add `--repair` only after each
repair has an unambiguous, independently tested safety contract.

### 2. Installation and first-run experience — must have

- [ ] Add `bm --version`.
- [ ] Improve the no-argument message with a short first-run workflow while preserving
  conventional help and error statuses.
- [ ] Document `pipx install bkmrk` alongside `uv tool install bkmrk` and pip.
- [ ] Verify and document shell completion setup.
- [ ] Run CI on the supported Python range.
- [ ] Run CI on Linux, macOS, and Windows.
- [ ] Add a release check that builds the wheel and source distribution and runs
  `twine check`.
- [ ] Decide whether a Homebrew formula belongs in 0.4.0 or a follow-up release.

### 3. Faster daily use — should have

#### Interactive selection

- [ ] Design `bm pick` as an optional `fzf` integration rather than a runtime dependency.
- [ ] Support existing filters such as tag, host, path, and since.
- [ ] Open the selected bookmark by default, with an option to print its ID or URL.
- [ ] Handle missing `fzf`, cancellation, and empty results cleanly.
- [ ] Keep JSON/JSONL pipelines as the stable noninteractive interface.

#### Capture helpers

- [ ] Accept a URL from standard input when explicitly requested.
- [ ] Investigate portable clipboard input without adding a runtime dependency.
- [ ] Add optional title fetching only if it has a bounded timeout, size limit, clear
  user agent, safe redirect policy, and deterministic failure behavior.
- [ ] Keep title fetching disabled unless the user requests it.
- [ ] Detect an existing normalized URL during add and offer a clear non-destructive path.

### 4. Browser capture and access — strategic 0.4.0 goal

A browser integration is compelling because capture friction is the largest difference
between `bm` and a built-in browser bookmark manager. Retrieval should be equally direct:
users should be able to search the live `bm` store and launch a result without leaving the
browser. The `.bm` files remain the source of truth; do not mirror the collection into the
browser's proprietary bookmark database or create two-way synchronization conflicts.

#### Discovery and architecture checkpoint

- [ ] Document the browser-to-CLI threat model.
- [ ] Compare browser Native Messaging with an authenticated loopback HTTP bridge.
- [ ] Evaluate setup complexity on Chromium, Firefox, Safari, macOS, Linux, and Windows.
- [ ] Select one transport before implementation.
- [ ] Define a small versioned request/response protocol for capture and read-only query
  actions.
- [ ] Capture messages contain URL, title, optional tags, notes, and destination path.
- [ ] Search messages contain query text, explicit filters, and a bounded result limit.
- [ ] Reject shell interpolation and validate all data at the existing CLI/store boundary.
- [ ] Keep browser code away from direct filesystem access; `bm` remains the only writer.
- [ ] Decide how the browser reports success, duplicates, validation failures, conflicts,
  and an unavailable local bridge.

**Preferred starting direction:** a Native Messaging host because it avoids an always-on
HTTP service and lets the browser invoke a narrowly scoped local program. Use one-shot
messages for capture and a connection that lives only while the search UI is open for
interactive queries. This remains a proposal until the cross-platform installation and
security costs are tested.

#### Minimum viable browser integration

Capture:

- [ ] Save the active tab's URL and title.
- [ ] Allow optional tags, destination path, and notes before saving.
- [ ] Show success, an existing bookmark, or a useful error in the browser.
- [ ] Preserve no-clobber behavior; never replace an existing bookmark silently.

Search and launch:

- [ ] Provide a popup command palette that searches title, URL, tags, and notes.
- [ ] Show recent bookmarks when the query is empty.
- [ ] Open the selected result in the current tab or a new tab.
- [ ] Add a keyboard shortcut for the command palette.
- [ ] Add an omnibox keyword such as `bm` for `Ctrl/Cmd+L → bm query → Enter`.
- [ ] Debounce interactive queries and bound result counts.
- [ ] Keep the native connection open only while the popup or search surface is active.
- [ ] Return bookmark metadata to the extension and let browser APIs open the URL.

Packaging and platform scope:

- [ ] Never send bookmark data to a hosted service.
- [ ] Request only minimal browser permissions such as `activeTab`, `nativeMessaging`, and
  local extension storage.
- [ ] Package installation, status, and removal commands with the integration.
- [ ] Support at least one Chromium-based browser for the first working slice.
- [ ] Determine whether Firefox support fits 0.4.0 after the transport is proven.
- [ ] Treat Safari support as a separate decision because its extension packaging differs.
- [ ] Treat a persistent side panel as a later enhancement, not an MVP requirement.

### 5. Safer deletion — should have

- [ ] Define trash retention and path layout without mixing trashed entries into normal
  list/search results.
- [ ] Add `bm trash <ID|path>` and `bm restore <ID|path>` or decide whether `bm rm` should
  move to trash by default.
- [ ] Preserve metadata, body, and original path.
- [ ] Handle destination conflicts without replacement.
- [ ] Provide an explicit permanent-delete operation.

**Decision required:** changing `bm rm` from permanent deletion would alter established
behavior. Do not make that change without a migration and compatibility decision.

### 6. Adoption and documentation — must have

- [ ] Add browser-specific import recipes for Chrome, Firefox, Safari, and Edge.
- [ ] Add an import preview example with counts for added, skipped, duplicate, and invalid
  entries.
- [ ] Add a concise comparison table for browser bookmarks, hosted bookmark services,
  Obsidian, and `bm`.
- [ ] Record a short terminal demo: install → add → search → inspect file → Git diff.
- [ ] Add Raycast, Alfred, and shell launcher recipes where they can remain small and
  dependency-free.
- [ ] Add `CONTRIBUTING.md` and identify bounded starter issues.
- [ ] Publish the `.bm` compatibility policy.
- [ ] Keep security and concurrency limitations explicit.

## Suggested implementation order

Each slice should be independently reviewed, validated, and committed.

1. **Release interface:** `--version`, first-run help, CI/build checks.
2. **Backup contract:** decide the JSON shape, then implement export/restore/dry-run.
3. **Diagnostics:** implement read-only `doctor` and recovery documentation.
4. **Daily use:** implement `pick` and bounded capture helpers.
5. **Browser discovery:** threat model and transport prototype.
6. **Browser MVP:** ship capture plus search/launch for one supported browser only after
   the prototype is reviewed.
7. **Deletion safety:** implement trash/restore if the compatibility decision is approved.
8. **Adoption docs:** migration guides, demo, comparison, and contributor setup.
9. **Release:** full cross-platform validation, package build, changelog, tag, and publish.

## Release acceptance criteria

Version 0.4.0 is ready when:

- [ ] Existing `.bm` files and CLI workflows remain compatible.
- [ ] A complete backup can be restored without losing paths, notes, timestamps, or unknown
  metadata.
- [ ] Restore and import previews make no filesystem changes.
- [ ] `bm doctor` can characterize a damaged or interrupted store without modifying it.
- [ ] Network access remains opt-in and bounded.
- [ ] Any browser bridge has a reviewed threat model and no hosted data path.
- [ ] Browser capture and search both use the live `bm` store without mirroring it into the
  browser bookmark database.
- [ ] Automated tests pass on the supported Python and operating-system matrix.
- [ ] Ruff, formatting, compile, package build, and package metadata checks pass.
- [ ] The README and command help match actual output and exit behavior.
- [ ] The changelog explains new commands, compatibility decisions, and remaining limits.

## Explicit non-goals for 0.4.0

- A general-purpose Obsidian replacement or knowledge graph.
- Backlinks, wiki links, or a plugin ecosystem.
- A hosted account or synchronization service.
- A database or search index required for normal operation.
- Silent background network fetching.
- Mobile applications.
- Automatic repair of ambiguous corruption.

## Open decisions

Track decisions here before implementation changes public behavior.

| Decision | Status | Default proposal |
|---|---|---|
| Lossless backup command shape | Open | Dedicated versioned backup/restore contract |
| Existing JSON export compatibility | Preserve | Do not silently change its schema |
| Initial `doctor` mutation authority | Decided | Read-only |
| Browser transport | Open | Prototype Native Messaging first |
| First supported browser | Open | One Chromium-based browser |
| Initial browser search UI | Open | Popup command palette plus omnibox keyword |
| Browser bookmark mirroring | Rejected | Keep `.bm` files as the only source of truth |
| Optional title fetching | Open | Explicit flag, bounded and off by default |
| `rm` versus trash behavior | Open | Preserve `rm` until compatibility is approved |
| Homebrew distribution | Open | Include only if release ownership is clear |

## Progress log

Add dated entries when a slice starts, completes, changes scope, or records a decision.

- **2026-08-23:** Created the 0.4.0 roadmap. Consolidated portability, diagnostics,
  daily-use, browser-capture, recovery, installation, and adoption work into a tracked plan.
- **2026-08-23:** Expanded browser integration to cover both directions: capture the active
  tab into `bm`, then search the live store and launch bookmarks through a popup command
  palette, keyboard shortcut, or omnibox keyword. Rejected mirroring into the browser's
  bookmark database.
