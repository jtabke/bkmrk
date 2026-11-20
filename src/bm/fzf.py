"""Terminal bookmark selection through the optional fzf executable."""

import os
import subprocess

from .errors import BmError
from .query import QueryRow


def pick_row(rows: list[QueryRow]) -> QueryRow:
    """Exchange only sanitized display rows with fzf, never shell commands."""
    choices = {}
    for index, row in enumerate(rows):
        display = "  ".join(str(row[key]) for key in ("path", "title", "url"))
        display = "".join(
            " " if ord(char) < 32 or 127 <= ord(char) < 160 else char for char in display
        )
        choices[f"{index}\t{display}"] = row
    env = os.environ.copy()
    # Custom fzf bindings/previews can execute shell commands or change its output
    # protocol. Keep this narrowly scoped picker independent of those settings.
    env.pop("FZF_DEFAULT_OPTS", None)
    env.pop("FZF_DEFAULT_OPTS_FILE", None)
    try:
        result = subprocess.run(
            [
                "fzf",
                "--no-multi",
                "--delimiter=\\t",
                "--with-nth=2..",
                "--prompt=bm> ",
                "--no-preview",
            ],
            input="\n".join(choices) + "\n",
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=env,
            check=False,
        )
    except FileNotFoundError as exc:
        raise BmError(
            "fzf is not installed; install fzf for interactive 'bm open', "
            "or provide an ID/path to open directly",
            exit_code=2,
        ) from exc
    except OSError as exc:
        raise BmError(f"could not start fzf: {exc}", exit_code=2) from exc
    if result.returncode in (1, 130):
        raise BmError(exit_code=result.returncode)
    if result.returncode:
        raise BmError(f"fzf failed with exit status {result.returncode}", exit_code=2)
    selection = result.stdout.rstrip("\r\n")
    if selection not in choices:
        raise BmError("fzf returned an invalid selection; no bookmark opened", exit_code=2)
    return choices[selection]
