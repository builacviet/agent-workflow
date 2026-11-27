"""Worker configuration and path validation regressions."""

import json

import pytest

from agent_workflow.application.exceptions import InvalidApplicationRequestError
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
    with pytest.raises(InvalidApplicationRequestError):
        safe_path(path)


def test_configuration_requires_explicit_scope():
    with pytest.raises(InvalidApplicationRequestError):
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
    with pytest.raises(InvalidApplicationRequestError, match="digest"):
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
    with pytest.raises(InvalidApplicationRequestError):
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
    with pytest.raises(InvalidApplicationRequestError):
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
    with pytest.raises(InvalidApplicationRequestError, match="v2 paths"):
        validate_config(sample)


@pytest.mark.parametrize(
    "directory",
    ["agent_workflow_v2", "workflow_console_v2", "workflow-console-v2"],
)
def test_actual_v2_directory_roots_stay_forbidden(tmp_path, directory):
    from agent_workflow.infrastructure.runtime.worker_config import independent_path

    with pytest.raises(InvalidApplicationRequestError, match="v2 paths"):
        independent_path(tmp_path / directory / "data/workflow.sqlite3")
