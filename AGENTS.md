# Agent Guide for bkmrk (`bm`)

Plain-text bookmarks with a standard-library-only core and optional `argcomplete` completion. Python >=3.8; source is `src/bm/`, tests are `tests/`. Use [README](README.md) for CLI contracts and storage behavior; tooling/style configuration lives in `pyproject.toml` and `prek.toml`.

## Implementation and store safety

- Use four spaces, double quotes, 100-character lines, sorted stdlib/third-party/local imports, type hints, snake_case functions/variables and PascalCase classes. Match existing docstrings; no bare `except`.
- Change the existing owner and keep cohesive code together. Preserve stable bookmark IDs, output contracts, atomic writes, no-clobber creates, optimistic conflict checks, and path/symlink safeguards.
- Use existing storage helpers; reject traversal, absolute bookmark paths, and escapes from the store. Atomic replacement uses same-directory temporary files and `os.replace()`; creates must not overwrite existing entries.
- CLI tests and smoke checks must select a disposable store using `BOOKMARKS_DIR` or `bm --store <temporary-directory> <command>`. Never mutate the default `~/.bookmarks.d` or personal store for QA. Isolate Git remotes and browser/editor side effects too; do not invoke `bm sync` against a real remote as a routine check.
- Preserve unrelated work and use one writer per checkout. New dependencies or changes to persisted format/public behavior need a demonstrated task requirement.

## Proportional checks

Run affected tests while iterating, for example `pytest tests/test_file.py::TestClass::test_method`. Run `ruff check <affected-files>` and `ruff format --check <affected-files>` for Python changes. Broader storage, shared CLI/output contracts, dependencies or test configuration need the relevant full tests and lint. Documentation-only edits need links, command accuracy, and whitespace checks; no application suite is required manually.

Commands:

- Syntax compilation: `python3 -m compileall src` (not package generation).
- Full tests: `pytest`.
- Full lint/format check: `ruff check .` and `ruff format --check .`.
- Scoped formatting: `ruff format <intended-files>`.
- Explicit full hook audit: `prek run --all-files`; do not make it the default for every edit. The installed commit hook currently runs pytest unconditionally, including documentation commits.

Review the final intended diff and report checks, untested cases, and residual risks. Do not weaken safeguards or assertions to clear a gate.

## Git and release

Use Conventional Commits. An authorized commit includes only intended files after checks; preserve unrelated staged/unstaged/untracked work. Push only when authorized.

Version bumps, changelog generation, tags, and releases are separate actions requiring explicit release authority. `cz bump` updates the version and configured changelog; `cz changelog` generates a changelog. Do not run these for an ordinary fix/docs commit. A request to push a documentation commit does not authorize version bumps or `git push --tags`.
