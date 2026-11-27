"""Trusted Git snapshot and export security regressions."""

import pytest

from agent_workflow.domain.exceptions import BusinessRuleError
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
    with pytest.raises(BusinessRuleError):
        changes(tmp_path, baseline, ["src"])
    (tmp_path / "AGENTS.md").write_bytes(b"changed policy")
    with pytest.raises(BusinessRuleError):
        changes(tmp_path, baseline, ["src", "docs"])


def test_export_reject_symlink(tmp_path):
    try:
        (tmp_path / "linked").symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("host symlink privilege unavailable")
    with pytest.raises(BusinessRuleError):
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
    with pytest.raises(BusinessRuleError):
        changes(tmp_path, {}, ["a.py", "b.py"])
