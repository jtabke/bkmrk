# Contributing to bm

Keep changes small and preserve the plain-text, standard-library-only core. Read the
[README](README.md), [agent/development guide](AGENTS.md), and
[compatibility policy](docs/compatibility.md) before changing storage or CLI behavior.

## Setup and checks

Python 3.12 or newer is required. Clone the repository, then set up with uv:

```sh
git clone https://github.com/jtabke/bkmrk
cd bkmrk
uv sync --locked --extra dev --extra build --python 3.15
uv run --locked --extra dev pytest tests/test_capture.py
uv run --locked --extra dev ruff check .
uv run --locked --extra dev ruff format --check .
uv run --locked --extra dev ty check
uv run --locked --extra dev pytest
uv run --locked python -m compileall src
```

Alternatively, create and activate a virtual environment, then run
`python -m pip install -e '.[dev,build]'`.
This alternative resolves dependencies rather than using the uv lockfile. Run the same
commands without the `uv run` prefixes. Follow the supported Python endpoints (3.12 and
3.15); CI also covers Linux, macOS, and Windows. Format only intended files with
`ruff format <files>`.

The lockfile includes development/build tools; the runtime stays standard-library-only.
Dependency resolution has a three-day release-age cooldown locally and in CI. Keep the
pinned Ruff version in `pyproject.toml` and `prek.toml` synchronized when updating it.

Type checking uses the pinned ty development dependency. Run `ty check` (with the uv
prefix above) to check `src/bm`; tests are not yet included. The configured target is
Python 3.12, the minimum supported version, independent of the active interpreter.
Platform assumptions are disabled so checks include platform-specific code paths.
CI runs the same check as a dedicated job. Update the ty pin and lockfile together,
and review diagnostics when upgrading; do not add blanket suppressions.

To run directly from the repository without installation, use `PYTHONPATH=src python -m bm --help`.
In PowerShell, use `$env:PYTHONPATH = "src"; python -m bm --help`.

For packaging changes, also build the wheel/source distribution and validate metadata:

```sh
uv build
uv run --locked --extra build twine check --strict dist/*
```

Smoke-test an installed wheel outside the checkout using a disposable store. Do not
publish distributions as part of an ordinary change. The optional full hook audit is
`prek run --all-files`; the installed commit hook currently runs pytest even for docs.

To inspect untested branches without enforcing a percentage threshold:

```sh
uv run --locked --extra dev pytest --cov=bm --cov-branch --cov-report=term-missing:skip-covered
```

## Safe local experiments

Always select a disposable store for tests and demos. In a POSIX shell:

```sh
work=$(mktemp -d)
bm --store "$work/store" init
bm --store "$work/store" add https://example.com --id example
bm --store "$work/store" show example
```

Use an equivalent fresh temporary directory on Windows. Never run QA against the default
`~/.bookmarks.d` or a personal store. Mock browser/editor launches and isolate Git remotes;
do not run routine `sync` tests against real remotes. Existing pytest fixtures (`store`,
`write_bm`, `bookmark_factory`, and `args_factory`) help isolate command and storage tests.
`bookmark_factory` can target different temporary roots for import/restore workflows.

## Architecture

The implementation keeps the filesystem backend concrete and separates responsibilities by seam:

- `bm.cli`: parser construction, `main(argv=None) -> int`, and process-boundary error/status handling
- `bm.commands`: thin command orchestration and user-facing output
- `bm.models`: open parsed-metadata types, typed field access, and runtime configuration
- `bm.store`: safe normalized/exact paths, entry traversal, snapshots, and filesystem mutations
- `bm.paths`: path syntax, reserved namespaces, filename rules, and literal prefix matching
- `bm.io`: tolerant front matter parsing with optional diagnostics, rendering, and atomic persistence
- `bm.query`: immutable filter values and list/search row logic
- `bm.netscape`: pure Netscape HTML import/export conversion
- `bm.importing`: best-effort browser-import execution and structured counts
- `bm.exporting`: browser/pipeline serializers that write to explicit streams
- `bm.backup`: versioned exact-content backups, stream output, and restore preflight/execution
- `bm.doctor`: read-only bookmark health diagnostics using the same parser and path rules
- `bm.dedupe`: pure merge rules, typed preview plans, and conflict-aware execution
- `bm.urls`: shared URL inspection; each operation retains its own accept/preserve/open policy
- `bm.clipboard`: explicit text clipboard access through optional OS utilities
- `bm.git` and `bm.editor`: hardened Git execution/diagnostics and platform-specific editor launching

Embedding code can call `bm.cli.main([...])` and inspect the returned status without catching `SystemExit`. The console script and `python -m bm` wrappers convert that status to the process exit code. Argument-parser help and usage errors retain their standard 0/2 statuses.

## Test organization

Keep tests with their feature owner rather than in chronological follow-up files:

- `test_cli_contracts.py`: parser wiring and shared process/output contracts.
- `test_capture.py` and `test_duplicate_capture.py`: capture and duplicate warnings.
- `test_clipboard.py`: OS helper selection, encoding, and subprocess safety;
  `test_copy.py`: the copy command's behavior.
- `test_open_workflows.py`: direct opening, terminal-gated selection, and fzf actions.
- `test_importing.py` and `test_backup.py`: browser import and lossless backup/restore.

Hypothesis properties live beside the existing examples in `test_io.py`,
`test_paths.py`, and `test_backup.py`. They cover tag round-trips, stable slug
normalization, contained destinations, and exact-content backup/restore. Keep generated
inputs bounded and filesystem state fresh for every example (for example, use
`TemporaryDirectory` inside the test, not a shared function-scoped pytest fixture).
Keep health checks enabled and add explicit regression cases for discovered bugs.

Pure query, path, serialization, and store tests remain beside their existing owners.
Preserve assertions and parametrized cases when moving tests; use the shared fixtures
instead of importing helpers from another test module.

## What to include in a change

- A focused regression test for behavior changes, including error/output contracts.
- Updated command examples when public behavior changes.
- Preservation of no-clobber creation, safe paths, atomic writes, stable IDs, unknown
  parsed metadata, and notes according to each operation's contract.
- Focused tests while iterating, then the applicable full tests/lint and a final diff review.
- A concise description of checks and any untested platforms or cases.

Use Conventional Commit messages, such as `fix: preserve notes during ...`. Version bumps,
changelog generation, tags, publishing, and pushes are separate authorized release actions.
Do not add runtime dependencies or persisted-format changes without a concrete requirement.

## Bounded starter tasks

These are candidate issue scopes, not claims that tracker issues already exist:

- Add one browser-export fixture exercising folder names, Unicode titles, and timestamps;
  verify import and preview agree without browser automation.
- Verify one platform's completion instructions in a clean shell and document the exact
  setup and shell version, without introducing a completion dependency into the core.
- Add a small launcher recipe around existing JSON/JSONL output, with quoted arguments,
  empty-result handling, and no automatic browser launch during tests.

Discuss public behavior changes before implementing them, particularly changes to permanent
deletion or automatic network access. An extension would require an explicit scope and
maintenance-ownership decision.
