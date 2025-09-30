# Agent Guidelines for bm (Bookmark Manager)

## Build/Lint/Test Commands

- **Build**: `python3 -m compileall src`
- **Lint**: `ruff check . && ruff format --check .`
- **Format**: `ruff format .`
- **Test all**: `pytest`
- **Test single**: `pytest tests/test_file.py::TestClass::test_method`
- **Pre-commit**: `pre-commit run --all-files`
- **Version bump**: `cz bump`
- **Generate changelog**: `cz changelog`
- **Release**: `cz bump --changelog && git push --tags`

## Code Style Guidelines

- **Line length**: 100 characters
- **Quotes**: Double quotes for strings
- **Indentation**: Spaces (4 spaces)
- **Imports**: stdlib → third-party → local, one per line
- **Types**: Use type hints extensively (dataclasses, functions)
- **Naming**: snake_case for functions/variables, PascalCase for classes
- **Docstrings**: Triple-quoted for modules, classes, and functions
- **Error handling**: Use exceptions appropriately, no bare except
- **File structure**: One class/function per logical unit, clear separation
- **Commit messages**: Use conventional commits format (feat, fix, docs, style, refactor, test, chore)

## Project Notes

- **Dependencies**: Stdlib-only (no third-party runtime deps)
- **Python version**: >=3.8
- **Atomic writes**: Use `os.replace()` for file modifications
- **Path safety**: Reject `..` and absolute paths in user input
