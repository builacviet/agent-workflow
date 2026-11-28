"""Trusted Git snapshot and export security regressions."""

import subprocess

import pytest

from agent_workflow.application.exceptions import (
    RuntimeAdmissionError,
    RuntimeContractError,
    RuntimeExecutionError,
)
from agent_workflow.workers.trusted_git import repository
from agent_workflow.workers.trusted_git.repository import changes


def test_export_only_allowlisted_changes(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_bytes(b"new")
    baseline = {
        "src/app.py": ("100644", b"old"),
        "docs/removed.md": ("100644", b"old"),
    }
    delta = changes(tmp_path, baseline, ["src", "docs"])
    assert delta == [
        {"path": "docs/removed.md", "mode": "100644", "content": None},
        {"path": "src/app.py", "mode": "100644", "content": b"new"},
    ]
    with pytest.raises(RuntimeAdmissionError):
        changes(tmp_path, baseline, ["src"])
    (tmp_path / "AGENTS.md").write_bytes(b"changed policy")
    with pytest.raises(RuntimeAdmissionError):
        changes(tmp_path, baseline, ["src", "docs"])


def test_export_reject_symlink(tmp_path):
    try:
        (tmp_path / "linked").symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("host symlink privilege unavailable")
    with pytest.raises(RuntimeAdmissionError):
        changes(tmp_path, {}, ["src"])


def test_snapshot_real_baseline_includes_safe_templates(tmp_path):
    import subprocess

    from agent_workflow.workers.trusted_git.repository import snapshot

    root = tmp_path / "source"
    root.mkdir()
    (root / ".runtime").mkdir()
    for path in ["AGENTS.md", ".env.example", ".runtime/mcp.env.example"]:
        (root / path).write_text("synthetic template")

    def git(*args):
        subprocess.run(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "-C",
                str(root),
                *args,
            ],
            check=True,
            capture_output=True,
        )

    git("init")
    git("add", ".")
    git("commit", "-m", "fixture")
    stage = tmp_path / "export"
    stage.mkdir()
    baseline = snapshot(root, "HEAD", stage)
    assert "AGENTS.md" in baseline
    assert ".env.example" in baseline
    assert ".runtime/mcp.env.example" in baseline
    assert not (stage / ".git").exists()
    assert not (stage / ".secret").exists()


def test_export_rejects_hardlinks(tmp_path):
    import os

    first = tmp_path / "a.py"
    first.write_text("fixture")
    os.link(first, tmp_path / "b.py")
    with pytest.raises(RuntimeAdmissionError):
        changes(tmp_path, {}, ["a.py", "b.py"])


@pytest.mark.parametrize("path", ["../outside.py", ".secret/key.pem", "CON.txt"])
def test_unsafe_paths_are_rejected_as_runtime_admission_errors(path):
    with pytest.raises(RuntimeAdmissionError, match="unsafe coder path"):
        repository.safe_path(path)


def test_independent_v2_paths_are_rejected_as_runtime_admission_error(tmp_path):
    with pytest.raises(RuntimeAdmissionError, match="v2 paths are forbidden"):
        repository.independent_path(
            tmp_path / "agent_workflow_v2" / "repo", directory=True
        )


@pytest.mark.parametrize(
    "failure",
    [OSError("synthetic private command output"), subprocess.TimeoutExpired("git", 2)],
)
def test_trusted_command_failures_are_sanitized_runtime_execution_errors(
    monkeypatch, failure
):
    def fail(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(repository.subprocess, "run", fail)
    with pytest.raises(
        RuntimeExecutionError, match="trusted command failed; output withheld"
    ) as error:
        repository.command(["git", "status"])
    assert error.value.__suppress_context__
    assert "private" not in str(error.value)


def test_snapshot_rejects_git_links_and_enforces_source_size_limit(
    monkeypatch, tmp_path
):
    destination = tmp_path / "links"
    destination.mkdir()
    monkeypatch.setattr(
        repository,
        "git",
        lambda *_args: b"120000 blob " + b"a" * 40 + b"\tlinked\0",
    )
    with pytest.raises(RuntimeAdmissionError, match="symlinks and submodules"):
        repository.snapshot(tmp_path, "a" * 40, destination)

    destination = tmp_path / "oversized"
    destination.mkdir()
    monkeypatch.setattr(repository, "MAX_BYTES", 1)
    monkeypatch.setattr(
        repository,
        "git",
        lambda _root, *_args: (
            b"100644 blob " + b"b" * 40 + b"\tfile.py\0"
            if _args[0] == "ls-tree"
            else b"xx"
        ),
    )
    with pytest.raises(RuntimeAdmissionError, match="source snapshot limit exceeded"):
        repository.snapshot(tmp_path, "a" * 40, destination)


def test_changes_rejects_linked_directory_unsafe_file_and_noop(monkeypatch, tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    try:
        (stage / "linked").symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pass
    else:
        with pytest.raises(RuntimeAdmissionError, match="linked directory"):
            changes(stage, {}, ["src"])
        (stage / "linked").unlink()
    monkeypatch.setattr(repository, "MAX_BYTES", 1)
    unsafe = stage / "bad.pem"
    unsafe.write_text("xx")
    with pytest.raises(RuntimeAdmissionError, match="unsafe file"):
        changes(stage, {}, ["src"])

    unsafe.unlink()
    with pytest.raises(RuntimeContractError, match="no source changes"):
        changes(stage, {}, ["src"])


def test_changes_enforces_file_count_limit(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    for index in range(2001):
        (stage / f"file-{index:04}.txt").write_bytes(b"x")
    with pytest.raises(RuntimeAdmissionError, match="snapshot file count exceeded"):
        changes(stage, {}, ["file"])
