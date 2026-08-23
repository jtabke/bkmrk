# Architecture hardening plan

## Objective

Improve bm's data safety, failure handling, testability, and module boundaries without changing its plain-text format, stdlib-only runtime, CLI compatibility, or single-filesystem-backend design.

## Decisions

- Preserve one `.bm` file per bookmark and unknown front-matter fields.
- Preserve current CLI output and exit codes unless a documented defect requires a change.
- Use optimistic conflict-aware mutations rather than relying only on a local process lock. Mutations that derive new content from an earlier read reject stale state observed before their final precondition check. Portable stdlib filesystem APIs do not provide an atomic content compare-and-swap, so a narrow external-writer race remains documented rather than hidden behind a stronger claim.
- Keep architecture concrete: no dependency-injection container, plugin system, ORM, abstract repository hierarchy, or new runtime dependency.
- Deliver the work as grouped conventional commits. Each group must pass focused tests, the full test suite, Ruff lint/format, fresh-context review, and parent diff inspection before commit.

## Baseline

- `pytest`: 255 passed
- `ruff check .`: passed
- `ruff format --check .`: passed
- Working tree: clean on `main`

## Group 1 — Persistence and input invariants (complete)

**Commit:** `fix: harden bookmark persistence invariants`

### Scope

1. Edit a temporary copy rather than the live bookmark.
2. Require editor success, validate the edited result, and commit with `atomic_write`.
3. Detect a stale original before committing an edited or tag-modified entry.
4. Make successful ISO parsing consistently timezone-aware.
5. Reject raw POSIX and Windows absolute bookmark paths before normalization.
6. Strengthen `atomic_write` with file fsync and best-effort directory fsync while retaining same-directory `os.replace` and symlink refusal.

### Acceptance

- Editor failure leaves the original byte-for-byte unchanged.
- Invalid edited content leaves the original unchanged.
- A concurrent change observed before the final optimistic precondition check is reported and not overwritten; the unavoidable external-writer check/replace race is documented.
- Naive, offset-aware, `Z`, and date-only timestamps can be compared safely.
- Absolute destination paths are rejected rather than rewritten as relative paths.
- Existing bookmark format and CLI success output remain compatible.

## Group 2 — External effects and interchange safety (complete)

**Commit:** `fix: bound external commands and escape exports`

### Scope

1. Centralize hardened Git execution used by init and sync.
2. Add bounded timeouts and clear timeout/failure diagnostics.
3. Ensure `init --git` reports success only after Git succeeds.
4. Keep Git invocation noninteractive where appropriate without weakening existing repository-config hardening.
5. Escape Netscape bookmark attributes, titles, and folder names correctly.
6. Add special-character import/export round-trip coverage.

### Acceptance

- Git failures and timeouts produce deterministic CLI errors.
- Sync cannot wait indefinitely for credentials or network response.
- Existing hardened Git configuration remains applied.
- Exported Netscape HTML is valid for quotes, ampersands, angle brackets, and non-ASCII text.

## Group 3 — Store and application boundary (complete)

**Commit:** `refactor: centralize store operations and application errors`

### Scope

1. Add focused application exceptions such as `BmError`, `UnsafePathError`, `NotFoundError`, and `ConflictError`.
2. Translate application exceptions to existing CLI messages and exit codes at the CLI boundary.
3. Resolve `BOOKMARKS_DIR` at runtime and pass a concrete store path into core operations.
4. Introduce a concrete `store.py` boundary for:
   - existence/create policy;
   - safe path construction and resolution;
   - entry iteration;
   - fingerprinted/conflict-aware writes and deletes;
   - move and empty-directory cleanup.
5. Keep one filesystem implementation; do not add repository interfaces.
6. Characterize and preserve intentional missing-store behavior before centralizing policy.

### Acceptance

- Path/domain helpers no longer print or raise `SystemExit`.
- Import handles unsafe paths through focused exceptions rather than catching `SystemExit`.
- Store selection responds to runtime environment changes.
- Resolution never returns a path that escapes the store.
- Dedupe verifies scanned entries remain unchanged before rewriting or deleting them.
- Existing CLI exit codes and messages remain stable where covered.

**Implementation note:** Store mutations perform optimistic byte-snapshot checks before
write/delete/move. The standard library does not provide a portable content
compare-and-swap across the final check and filesystem operation, so an external
writer can still race that narrow window. Multi-file operations verify the full
scan before starting but may retain safe partial progress if a later per-file
check conflicts; callers must treat `ConflictError` as a retry/reconciliation
signal rather than as a transaction guarantee.

## Group 4 — Query and format boundaries (complete)

**Commit:** `refactor: split query and interchange concerns`

### Scope

1. Add a small immutable `FilterSpec` built once from CLI arguments.
2. Remove `MagicMock`-specific argument accommodation from production filtering.
3. Move pure list/search/filter/result-row logic to `query.py`.
4. Move Netscape parsing/rendering to `netscape.py`.
5. Move the cohesive dedupe selection and merge decisions to `dedupe.py`; keep filesystem scanning and mutation orchestration in `commands.py`.
6. Keep `commands.py` as thin orchestration adapters rather than merely redistributing unchanged command functions.
7. Remove the unused `Bookmark` dataclass unless it becomes a real canonical type while preserving unknown metadata.

### Acceptance

- `commands.py` no longer owns parsing/rendering for Netscape data or low-level store traversal.
- Query functions accept explicit typed values rather than arbitrary argparse-like objects.
- Pure query/interchange behavior remains independently testable.
- No generic service layer or catch-all replacement module is introduced.
- Dedupe's pure selection/merge rules are independently housed in `dedupe.py`; command-level scan/write/delete orchestration remains in `commands.py`.

## Group 5 — CLI contracts and documentation (complete)

**Commit:** `test: add end-to-end CLI architecture contracts`

### Scope

1. Make `main(argv=None) -> int` directly testable while preserving console-script and `python -m bm` behavior.
2. Add a compact real-CLI contract matrix covering:
   - representative mutation success;
   - invalid path and not-found failures;
   - JSON and JSONL output;
   - no-result behavior;
   - editor/Git failure mapping;
   - conflict detection.
3. Update README robustness statements to distinguish atomic visibility, crash durability, and conflict detection.
4. Document the module boundaries and concurrency guarantee.
5. Update roadmap/development text that no longer reflects the test suite.

### Acceptance

- Parser wiring and real output/exit behavior are exercised without duplicating every unit test as a subprocess test.
- README claims match implemented behavior.
- Final full validation passes on Python 3.8-compatible syntax.

## Final architecture

The completed implementation has these concrete seams:

- `cli.py`: parser construction, `main(argv=None) -> int`, and process-boundary status/error translation.
- `commands.py`: command orchestration, external-effect adapters, and presentation.
- `store.py`: filesystem paths, traversal, optimistic snapshots, and mutations.
- `io.py`: front matter and atomic persistence.
- `query.py`: immutable filters and list/search row logic.
- `netscape.py`: Netscape HTML conversion.
- `dedupe.py`: pure duplicate selection and merge policy.

The concurrency contract is optimistic rather than transactional: mutations reject
stale snapshots observed before their final check; portable stdlib operations cannot
close the final check/replace race, and multi-file operations can retain safe partial
progress after a later conflict. `ConflictError` is therefore a retry/reconciliation
signal.

Group 5 adds a compact real-CLI contract matrix and makes `main(argv)` return statuses
for embedding/tests. The console script and `python -m bm` remain the process-exit
wrappers. Runtime remains stdlib-only and the `.bm` format/unknown metadata behavior is
unchanged.

No-force creation and moves use hard-link publication without replacement. Unsupported
hard links fail explicitly. A crash after creation publication may leave a temporary
hard-link name; a crash during link-then-unlink move may leave both names. Neither case
overwrites an existing destination.

## Per-group workflow

For each group:

1. Capture focused baseline tests for the affected behavior.
2. Use one writer for the active checkout.
3. Run focused tests, full `pytest`, `python3 -m compileall src`, `ruff check .`, and `ruff format --check .` with bounded subprocess timeouts.
4. Launch fresh-context reviewers with distinct correctness/robustness and simplicity/testability angles.
5. Synthesize findings and apply only fixes within the approved group.
6. Repeat focused review when fixes are substantial.
7. Inspect the final diff and ensure no files are staged unexpectedly.
8. Commit the complete logical group using the stated conventional commit message.

## Stop and escalation rules

Stop before committing if a slice would:

- change the bookmark file format or drop unknown metadata;
- require a runtime dependency;
- intentionally change established CLI output or exit codes beyond a confirmed defect;
- require a cross-platform guarantee unavailable in supported Python versions;
- expose an unapproved product or compatibility decision.
