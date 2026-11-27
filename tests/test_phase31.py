"""Deterministic Phase 3.1 boundaries; no external credentials or model calls."""

import json

import pytest

from agent_workflow.domain.exceptions import BusinessRuleError
from agent_workflow.infrastructure.runtime.worker_config import (
    safe_path,
    validate_config,
)
from agent_workflow.workers.trusted_git.app_auth import REPOSITORY


@pytest.mark.parametrize(
    "path",
    [
        "../escape",
        "/abs",
        "a/../b",
        "a\\b",
        ".secret/key.pem",
        "src/key.key",
        ".git/config",
        ".env.production",
        "a//b",
        "AGENTS.md",
        ".codex/config.toml",
    ],
)
def test_reject_unsafe_paths(path):
    with pytest.raises(BusinessRuleError):
        safe_path(path)


def test_configuration_requires_explicit_scope():
    with pytest.raises(BusinessRuleError):
        validate_config({"repository": "other/repo", "tasks": {}})


@pytest.fixture
def config(tmp_path):
    import hashlib

    secret = tmp_path / ".secret"
    secret.mkdir()
    (secret / "app.pem").write_text("fixture only")
    (secret / "openai.txt").write_text("fixture only")
    worktree = tmp_path / "task"
    worktree.mkdir()
    (worktree / ".git").write_text("fixture worktree marker")
    profile = tmp_path / "coder-seccomp.json"
    profile.write_text("{}")
    return {
        "repository": REPOSITORY,
        "app_id": 1,
        "installation_id": 2,
        "database": str(tmp_path / "state.sqlite3"),
        "pem": str(secret / "app.pem"),
        "codex_auth_volume": "techlead-review-poc_codex_auth",
        "scratch": str(tmp_path / "jobs"),
        "seccomp_profile": str(profile),
        "seccomp_sha256": hashlib.sha256(profile.read_bytes()).hexdigest(),
        "image": "my-wfc-codex:fixture",
        "model": "fixture-only",
        "timeout_seconds": 60,
        "tasks": {
            "P31": {
                "worktree": str(worktree),
                "write_paths": ["src"],
                "handoff_path": "docs/handoff.md",
            }
        },
    }


def test_valid_config_and_profile_digest(config):
    validate_config(config)
    config["seccomp_sha256"] = "0" * 64
    with pytest.raises(BusinessRuleError, match="digest"):
        validate_config(config)


@pytest.mark.parametrize(
    "key,value",
    [
        ("repository", "other/agent-workflow"),
        ("repository", "builacviet/techlead-review-poc"),
        ("codex_auth_volume", "workflow-console-v2-codex-auth-20261029"),
        ("scratch", "relative"),
        ("image", "bad image"),
        ("model", ""),
        ("timeout_seconds", 60.5),
        ("database", "v2-path"),
    ],
)
def test_invalid_config_boundaries(config, key, value, tmp_path):
    if value == "same-pem":
        value = config["pem"]
    if value == "v2-path":
        value = str(tmp_path / "agent_workflow_v2" / "data.sqlite3")
    config[key] = value
    with pytest.raises(BusinessRuleError):
        validate_config(config)


@pytest.mark.parametrize(
    "path",
    [
        ".GIT/config",
        ".SECRET/key.txt",
        ".Env.production",
        "src/NUL.py",
        "src/trailing.",
    ],
)
def test_cross_platform_path_aliases_are_rejected(path):
    with pytest.raises(BusinessRuleError):
        safe_path(path)


def test_documented_shared_database_configuration(config, tmp_path):
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    sample = json.loads((root / ".runtime/controller.json.example").read_text())
    assert sample["database"].endswith("data/controller/workflow.sqlite3")
    for key in (
        "pem",
        "codex_auth_volume",
        "scratch",
        "seccomp_profile",
        "seccomp_sha256",
    ):
        sample[key] = config[key]
    sample["database"] = str(tmp_path / "data/controller/workflow.sqlite3")
    sample["tasks"] = config["tasks"]
    validate_config(sample)
    sample["database"] = str(tmp_path / "agent_workflow_v2/data/workflow.sqlite3")
    with pytest.raises(BusinessRuleError, match="v2 paths"):
        validate_config(sample)


@pytest.mark.parametrize(
    "directory",
    ["agent_workflow_v2", "workflow_console_v2", "workflow-console-v2"],
)
def test_actual_v2_directory_roots_stay_forbidden(tmp_path, directory):
    from agent_workflow.infrastructure.runtime.worker_config import independent_path

    with pytest.raises(BusinessRuleError, match="v2 paths"):
        independent_path(tmp_path / directory / "data/workflow.sqlite3")


def test_cleanup_remains_available_after_deadline(monkeypatch, tmp_path):
    import agent_workflow.budget as budget
    from agent_workflow.infrastructure.runtime import docker_runtime as runtime

    now = [1000]
    monkeypatch.setattr(budget.time, "time", lambda: now[0])

    def command(*args, **kwargs):
        assert budget.operation_timeout(30) == 30
        return b""

    from agent_workflow.infrastructure.runtime import sandbox

    monkeypatch.setattr(sandbox, "docker_command", command)
    monkeypatch.setattr(sandbox, "_require_engine", lambda: "fixture-daemon-a")
    from unittest.mock import Mock

    controller_runtime = runtime.DockerRuntime(
        {},
        dispatch=Mock(),
        leadership_guard=lambda: True,
        sandbox=sandbox.SandboxManager(tmp_path / "sandbox-fences"),
    )
    controller_runtime.sandbox.record_engine(
        "a" * 32, "unix:///fixture.sock", "fixture-daemon-a"
    )
    with budget.dispatch_budget(1001):
        now[0] = 1002
        controller_runtime.stop("a" * 32)
        with pytest.raises(TimeoutError):
            budget.check_deadline()


def test_wall_clock_rollback_does_not_extend_budget(monkeypatch):
    import agent_workflow.budget as budget

    wall, monotonic = [1000], [500]
    monkeypatch.setattr(budget.time, "time", lambda: wall[0])
    monkeypatch.setattr(budget.time, "monotonic", lambda: monotonic[0])
    with budget.dispatch_budget(1010):
        wall[0] = 900
        monotonic[0] = 511
        with pytest.raises(TimeoutError):
            budget.check_deadline()
