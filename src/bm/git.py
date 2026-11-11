"""Hardened, bounded Git integration and read-only metadata diagnostics."""

import os
import re
import subprocess
from pathlib import Path
from typing import List

from .doctor import DoctorReport
from .errors import BmError
from .store import Store

# Neutralize store-local `.git/config` keys that can run arbitrary commands
# (CVE-2022-39253 family: core.fsmonitor / core.sshCommand / core.hooksPath).
# `-c` overrides win after the per-repo config is read.
_GIT_HARDENED_PREFIX = (
    "git",
    "-c",
    "core.fsmonitor=",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.sshCommand=",
    "-c",
    "credential.helper=",
    "-c",
    "protocol.file.allow=user",
)
_GIT_TIMEOUT_SECONDS = 30


def git_command(*args: str) -> List[str]:
    return [*_GIT_HARDENED_PREFIX, *args]


def run_git(
    store: Path,
    *args: str,
    check: bool = True,
    capture_output: bool = False,
) -> subprocess.CompletedProcess:
    """Run a hardened, bounded, non-interactive Git command.

    All Git subprocesses go through this seam so repository-local configuration
    cannot re-enable command execution and network operations cannot wait
    forever for credentials or a remote response.
    """
    command = git_command(*args)
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        result = subprocess.run(
            command,
            cwd=store,
            check=check,
            capture_output=capture_output,
            text=capture_output,
            timeout=_GIT_TIMEOUT_SECONDS,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise BmError(
            f"git command timed out after {_GIT_TIMEOUT_SECONDS}s: {' '.join(command)}",
            exit_code=124,
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise BmError(
            f"git command failed ({' '.join(command)}): exit {exc.returncode}",
            exit_code=exc.returncode or 1,
        ) from exc
    except OSError as exc:
        raise BmError(f"git command failed ({' '.join(command)}): {exc}", exit_code=2) from exc

    # ``check=True`` guarantees this for the real subprocess implementation;
    # retaining the explicit check keeps the seam deterministic for callers
    # that provide a subprocess test double.
    if check and result.returncode:
        raise BmError(
            f"git command failed ({' '.join(command)}): exit {result.returncode}",
            exit_code=result.returncode or 1,
        )
    return result


def inspect_git(store: Store, report: DoctorReport) -> None:
    """Check local Git discovery, HEAD shape, and locks without status/index writes."""
    marker = store.root / ".git"
    if marker.is_symlink():
        report.add(
            "git_symlink",
            [".git"],
            "Git metadata is a symlink and was not inspected.",
            "Inspect the link manually before running Git commands.",
        )
        return
    if not marker.exists():
        return
    try:
        result = run_git(store.root, "rev-parse", "--absolute-git-dir", capture_output=True)
        git_dir = Path(result.stdout.strip())
        head = git_dir / "HEAD"
        if head.is_symlink():
            raise BmError("Git HEAD is a symlink")
        text = head.read_text(encoding="utf-8").strip()
        if text.startswith("ref: "):
            run_git(store.root, "check-ref-format", text[5:], capture_output=True)
        elif not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", text):
            raise BmError("Git HEAD is malformed")
        for name in ("index.lock", "HEAD.lock", "config.lock", "packed-refs.lock", "shallow.lock"):
            path = git_dir / name
            if path.exists() or path.is_symlink():
                report.add(
                    "git_lock",
                    [f".git/{name}"],
                    "Git lock file is present.",
                    "Check for active Git processes before considering manual lock removal.",
                    warning=True,
                )
    except (BmError, OSError, UnicodeError) as exc:
        report.add(
            "git_problem",
            [".git"],
            str(exc),
            "Inspect Git metadata/configuration manually; doctor never changes it.",
        )
