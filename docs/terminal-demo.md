# Short terminal demo

[Download the recording](terminal-demo.cast), or replay it locally from the checkout:

```sh
asciinema play docs/terminal-demo.cast
```

An asciicast-compatible player is optional viewing software, not a bm dependency. Playback
only displays recorded output; it does not install packages, execute the displayed commands,
access your clipboard, or open a browser. The recording is about 20 seconds long; pause it
when inspecting the bookmark file or Git diff.

## What the recording shows

1. Install a wheel into a fresh temporary Python environment.
2. Initialize a disposable bookmark store with a local Git repository.
3. Add the bkmrk repository with a title, tags, notes, and an explicit path.
4. Search its title/URL/tags/notes and retrieve the URL without launching a browser.
5. Read the underlying `.bm` file directly with Python.
6. Inspect the new file with `git diff`, without committing or contacting a remote.

These are actual command outputs from the current working-tree wheel, with paced prompts
and pauses added for readability. Temporary paths are replaced with `$DEMO`; no interactive
keystrokes or personal environment variables are recorded. The wheel still reports version
0.3.1 because the version has not been bumped: this is an unreleased working-tree demo,
**not** a claim that all shown features are in the published 0.3.1 package.

The install step includes uv's harmless cross-filesystem hard-link warning: it fell back
to copying and installed successfully. It is unrelated to bm's no-clobber bookmark writes.
The temporary environment, wheel/build files, Git repository, and store were removed after
recording. No personal bookmarks, clipboard contents, editor, or browser were used.

## Reproduce the walkthrough

Use an installed checkout wheel or a version containing the shown commands. In a POSIX
shell, select a disposable store before running any bm command:

```sh
DEMO=$(mktemp -d)
export BOOKMARKS_DIR="$DEMO/store"

bm init --git
bm add https://github.com/jtabke/bkmrk \
    --name "bkmrk on GitHub" --tags cli,bookmarks \
    --description "Source and documentation for bm." --id tools/bkmrk
bm search source documentation
bm open tools/bkmrk --print url
python -c 'import os; from pathlib import Path; print((Path(os.environ["BOOKMARKS_DIR"]) / "tools/bkmrk.bm").read_text(), end="")'
git -C "$BOOKMARKS_DIR" add --intent-to-add tools/bkmrk.bm
git -C "$BOOKMARKS_DIR" diff -- tools/bkmrk.bm
```

This creates only a local demonstration store and Git index. No commit, push, sync, or
browser launch is needed. The temporary directory is left available for inspection; remove
that specific directory when finished, and unset `BOOKMARKS_DIR` to return to your normal
store. The Python environment used for recording was separately created under `$DEMO/venv`
and installed from a local wheel; ordinary installation options are in the
[README](../README.md#install).

To try interactive navigation separately, use `bm open --search bookmarks` from a terminal
with optional `fzf` installed. Enter confirms a selection; Escape/Ctrl-C cancel. This opens
a browser unless you pass `--print url` or `--copy`. Copying replaces the system clipboard;
clipboard history may retain the URL. Clipboard capture and interactive navigation are not
part of this recording, so playback never exposes or modifies your live clipboard.
