"""Lint and format only changed Python files in GitHub Actions.

The comparison uses the actual PR head, not a synthetic merge commit.
A first branch push compares against its merge-base with origin/main.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

ZERO_SHA = "0" * 40


def _git_bytes(*args: str) -> bytes:
    return subprocess.check_output(["git", *args])


def _sha(value: str | None, label: str) -> str:
    if value is None or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise RuntimeError(f"Missing or invalid {label} commit SHA")
    return value


def commit_range(environ: Mapping[str, str]) -> tuple[str, str]:
    event = environ.get("GITHUB_EVENT_NAME")
    if event == "pull_request":
        return (
            _sha(environ.get("RUFF_PR_BASE_SHA"), "PR base"),
            _sha(environ.get("RUFF_PR_HEAD_SHA"), "PR head"),
        )
    if event == "push":
        head = _sha(environ.get("GITHUB_SHA"), "push head")
        before = environ.get("RUFF_PUSH_BEFORE_SHA")
        if before and before != ZERO_SHA:
            return _sha(before, "push before"), head
        branch = environ.get("RUFF_DEFAULT_BRANCH", "main")
        if not re.fullmatch(r"[A-Za-z0-9._/-]+", branch):
            raise RuntimeError("Invalid default branch")
        base = (
            _git_bytes("merge-base", head, f"refs/remotes/origin/{branch}")
            .decode("ascii")
            .strip()
        )
        return _sha(base, "merge-base"), head
    raise RuntimeError("Ruff CI supports only push and pull_request events")


def changed_python_files(base: str, head: str) -> list[str]:
    changed = _git_bytes(
        "diff", "--name-only", "-z", "--diff-filter=ACMRT", base, head, "--"
    )
    paths = sorted(
        os.fsdecode(path)
        for path in changed.split(b"\0")
        if path and path.endswith(b".py")
    )
    for path in paths:
        file = Path(path)
        if file.is_symlink() or not file.is_file():
            raise RuntimeError("Changed Python file is missing or a symlink")
    return paths


def run_checks(paths: list[str]) -> int:
    if not paths:
        print("Ruff: no changed Python files")
        return 0
    print("Ruff: checking", ", ".join(repr(path) for path in paths), flush=True)
    failed = False
    for command in (["check"], ["format", "--check"]):
        result = subprocess.run(
            [sys.executable, "-m", "ruff", *command, "--", *paths],
            check=False,
        )
        failed = failed or result.returncode != 0
    return 1 if failed else 0


def main() -> int:
    try:
        base, head = commit_range(os.environ)
        return run_checks(changed_python_files(base, head))
    except (OSError, RuntimeError, subprocess.CalledProcessError, UnicodeError):
        print("Ruff: failed to determine safe changed-file range", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
