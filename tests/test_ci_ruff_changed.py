"""Offline regression tests for the incremental Ruff CI selector."""
from __future__ import annotations

import subprocess
import sys

import pytest

from scripts import ci_ruff_changed as gate


A = "a" * 40
B = "b" * 40
C = "c" * 40


def test_pr_uses_real_base_and_head() -> None:
    assert gate.commit_range(
        {
            "GITHUB_EVENT_NAME": "pull_request",
            "RUFF_PR_BASE_SHA": A,
            "RUFF_PR_HEAD_SHA": B,
        }
    ) == (A, B)


def test_existing_push_uses_before_and_head() -> None:
    assert gate.commit_range(
        {
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_SHA": B,
            "RUFF_PUSH_BEFORE_SHA": A,
        }
    ) == (A, B)


def test_first_push_uses_default_branch_merge_base(monkeypatch) -> None:
    calls = []

    def git(*args):
        calls.append(args)
        return (C + "\n").encode()

    monkeypatch.setattr(gate, "_git_bytes", git)
    assert gate.commit_range(
        {
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_SHA": B,
            "RUFF_PUSH_BEFORE_SHA": gate.ZERO_SHA,
            "RUFF_DEFAULT_BRANCH": "main",
        }
    ) == (C, B)
    assert calls == [("merge-base", B, "refs/remotes/origin/main")]


@pytest.mark.parametrize(
    "env",
    [
        {"GITHUB_EVENT_NAME": "schedule"},
        {"GITHUB_EVENT_NAME": "pull_request", "RUFF_PR_BASE_SHA": A},
        {"GITHUB_EVENT_NAME": "push", "GITHUB_SHA": "--bad"},
        {
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_SHA": B,
            "RUFF_DEFAULT_BRANCH": "../main@{x}",
        },
    ],
)
def test_invalid_ranges_fail_closed(env) -> None:
    with pytest.raises(RuntimeError):
        gate.commit_range(env)


def test_changed_python_paths_are_nul_delimited(monkeypatch, tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "new.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "src" / "a space.py").write_text("x = 2\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        gate,
        "_git_bytes",
        lambda *args: b"src/new.py\0src/a space.py\0docs/readme.md\0",
    )
    assert gate.changed_python_files(A, B) == [
        "src/a space.py",
        "src/new.py",
    ]


def test_deleted_or_missing_python_file_fails_closed(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gate, "_git_bytes", lambda *args: b"gone.py\0")
    with pytest.raises(RuntimeError):
        gate.changed_python_files(A, B)


def test_no_python_changes_skips_ruff(monkeypatch) -> None:
    monkeypatch.setattr(
        gate.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("Ruff must not run"),
    )
    assert gate.run_checks([]) == 0


def test_lint_and_format_both_run_even_if_lint_fails(monkeypatch) -> None:
    calls = []

    def run(command, *, check):
        calls.append((command, check))
        return subprocess.CompletedProcess(command, 1 if len(calls) == 1 else 0)

    monkeypatch.setattr(gate.subprocess, "run", run)
    assert gate.run_checks(["src/a.py"]) == 1
    assert calls == [
        ([sys.executable, "-m", "ruff", "check", "--", "src/a.py"], False),
        (
            [
                sys.executable,
                "-m",
                "ruff",
                "format",
                "--check",
                "--",
                "src/a.py",
            ],
            False,
        ),
    ]
