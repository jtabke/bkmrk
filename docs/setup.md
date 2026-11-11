# Setup and platform notes

See the [installation instructions](../README.md#install) for pip, pipx, and uv tool installs.
The core requires Python 3.12 or newer and has no third-party runtime dependencies.
Basic store, editor, and debug settings are in [configuration](../README.md#configuration).
For development environments and checks, use [CONTRIBUTING.md](../CONTRIBUTING.md).

## Interactive selection

Install `fzf` through your system package manager for bare `bm open` or
`bm open --search "terms"`. Selection requires terminals on stdin and stderr;
stdout may be redirected for `--print`. Even one match needs confirmation.
Direct lookup with `bm open <ID|path>`, `bm copy <ID|path>`, or
`bm open <ID|path> --print url` does not require fzf or a terminal.
See the [open command contract](../README.md#show-and-open).

## Clipboard setup

Clipboard access is opt-in: only `add --clipboard`, `copy`, and `open --copy` use it. The core
has no new Python dependency. Available platform helpers are selected automatically:

- **macOS:** `pbpaste` for reading, `pbcopy` for writing.
- **Windows:** `powershell.exe`, or `pwsh.exe` if unavailable, using fixed UTF-8 scripts with
  profiles disabled. Copied text goes through stdin, never command interpolation.
- **Linux/Unix desktops:** prefer `wl-paste`/`wl-copy` from `wl-clipboard` when
  `WAYLAND_DISPLAY` is set; otherwise use available `xclip` or `xsel` with `DISPLAY` set.
  In a mixed Wayland/X11 session, an unavailable Wayland utility can fall back to X11.
  Only the regular clipboard is used, not the primary selection.

Helpers are resolved from PATH and run without a shell. A selected helper failing does not
trigger a retry with another helper. Missing helpers, unavailable desktop sessions, helper
failures, and three-second timeouts produce actionable errors (exit 2). Text must be UTF-8,
contain no NUL bytes, and fit within 64 KiB; for reads, this size check happens after the helper
returns. Clipboard contents and helper stderr are not included in failure diagnostics.
Headless/SSH sessions need a working forwarded desktop clipboard; otherwise use `bm add -`
and `bm show`, `bm search --json <terms>`, or `bm open <ID|path> --print url` instead.

Copying replaces your clipboard contents. Clipboard history managers may retain URLs;
`bm` does not clear clipboard history or promise timed erasure. No clipboard watcher,
browser extension, hosted service, or automatic network fetch is involved.

## Shell completion

Completion is optional and uses [argcomplete](https://github.com/kislyuk/argcomplete).
Install the extra with your chosen installer:

```bash
pip install 'bkmrk[completion]'
# Or, for an isolated CLI installation:
pipx install 'bkmrk[completion]'
# Or:
uv tool install 'bkmrk[completion]'
```

The registration helper must run in the same environment as `bm`. With pipx, expose
its helper commands using `pipx install --include-deps 'bkmrk[completion]'` instead;
with uv, use `uv tool install --with-executables-from argcomplete 'bkmrk[completion]'`.

For Bash, add this to `~/.bashrc`:

```bash
eval "$(register-python-argcomplete --shell bash bm)"
```

For Zsh, add this to `~/.zshrc` (omit `compinit` if your configuration already runs it):

```zsh
autoload -Uz compinit
compinit
eval "$(register-python-argcomplete --shell zsh bm)"
```

Start a new shell (or reload its configuration), then type `bm se` and press Tab to
complete `search`. This registers only `bm`; global completion activation is not required.
These instructions cover Bash and Zsh, not PowerShell.

## Windows notes

- Bookmark paths accept `/` or `\\` separators on every platform. Commands and JSON/Netscape exports use `/` for consistent logical paths; store and import-file locations remain native filesystem paths.
- Absolute, drive-qualified (including `C:relative`), and parent-traversal bookmark paths are rejected before normalization.
- CI runs the full test suite on Linux, macOS, and Windows with Python 3.12 (the minimum) and 3.15.
- Normalized bookmark filenames are checked for reserved device names (such as `CON`, `NUL`, and `COM1`) and unsupported components on Windows. Invalid imported folder paths are reported and skipped; valid entries still import. Existing POSIX filenames and URL-derived bookmark IDs are unchanged.
- The default Windows editor is Notepad. Set `VISUAL` or `EDITOR` to an executable and arguments, using Windows double quotes around paths with spaces. Use the editor's blocking mode (for example, `$env:EDITOR = 'code.cmd --wait'`) so editing finishes before `bm` saves the result. `VISUAL` takes precedence over `EDITOR`.
- Windows `.cmd`/`.bat` editor launchers are supported with quoted arguments and shell expansion disabled. Arguments or paths containing double quotes, `%`, `!`, or line breaks are rejected for batch launchers; use a native `.exe` editor for those paths. Native editor launches do not use a shell.
- Atomic replacement and no-clobber operations use the corresponding Windows filesystem APIs. Long-path limits and filesystem capabilities still apply.
