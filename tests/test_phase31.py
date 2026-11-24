"""Deterministic Phase 3.1 boundaries; no external credentials or model calls."""

import base64
import json

import pytest

from agent_workflow.integrations.github import LEGACY_REPOSITORY, WorkflowError
from agent_workflow.runners.coder_runtime import safe_path, validate_config
from agent_workflow.workers.trusted_git.app_auth import (
    BOT,
    REPOSITORY,
    AppVerifier,
    GitHubApp,
)
from agent_workflow.workers.trusted_git.repository import changes

BASE = "a" * 40
HEAD = "b" * 40


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
    with pytest.raises(WorkflowError):
        safe_path(path)


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
    with pytest.raises(WorkflowError):
        changes(tmp_path, baseline, ["src"])
    (tmp_path / "AGENTS.md").write_bytes(b"changed policy")
    with pytest.raises(WorkflowError):
        changes(tmp_path, baseline, ["src", "docs"])


def test_export_reject_symlink(tmp_path):
    try:
        (tmp_path / "linked").symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("host symlink privilege unavailable")
    with pytest.raises(WorkflowError):
        changes(tmp_path, {}, ["src"])


class Transport:
    def __init__(self, now):
        self.calls = []
        self.now = now
        self.slug = "builacviet-wfc-coder"
        self.head = HEAD

    def __call__(self, method, endpoint, token, payload=None):
        self.calls.append((method, endpoint, payload))
        if endpoint == "/app":
            return {"id": 1, "slug": self.slug}
        if endpoint.endswith("/installation"):
            return {"id": 2, "app_id": 1, "suspended_at": None}
        if endpoint.endswith("/access_tokens"):
            from datetime import datetime, timezone

            return {
                "token": "synthetic-only",
                "expires_at": datetime.fromtimestamp(
                    self.now[0] + 3600, timezone.utc
                ).isoformat(),
            }
        if endpoint == "/users/" + BOT:
            return {"id": 123, "login": BOT, "type": "Bot"}
        if "/branches/" in endpoint:
            return {"commit": {"sha": self.head}}
        if "/compare/" in endpoint:
            return {"status": "ahead"}
        return None


@pytest.fixture
def app(tmp_path):
    secret = tmp_path / ".secret"
    secret.mkdir()
    pem = secret / "fixture.pem"
    pem.write_text("not a real key")
    now = [1000]
    transport = Transport(now)
    client = GitHubApp(
        1,
        2,
        pem,
        transport=transport,
        signer=lambda *args: b"signature",
        clock=lambda: now[0],
    )
    return client, transport, now


def test_jwt_cache_refresh_scope_and_identity(app):
    client, transport, now = app
    claims = json.loads(base64.urlsafe_b64decode(client.jwt().split(".")[1] + "=="))
    assert claims == {"iat": 940, "exp": 1540, "iss": "1"}
    assert client.token() == "synthetic-only"
    assert client.token() == "synthetic-only"
    assert len(transport.calls) == 3
    assert transport.calls[-1][2] == {
        "repositories": ["agent-workflow"],
        "permissions": {"contents": "write", "pull_requests": "write"},
    }
    assert client.identity() == (
        BOT,
        "123+" + BOT + "@users.noreply.github.com",
    )
    now[0] += 3500
    client.token()
    assert (
        sum(endpoint.endswith("/access_tokens") for _, endpoint, _ in transport.calls)
        == 2
    )
    client.revoke()
    assert client._token == ""


def test_app_identity_and_stale_sha_fail_closed(app):
    client, transport, _ = app
    transport.slug = "wrong-app"
    with pytest.raises(WorkflowError):
        client.token()
    transport.slug = "builacviet-wfc-coder"
    verifier = AppVerifier(client)
    verifier(LEGACY_REPOSITORY, "codex/test", BASE, HEAD)
    assert (
        transport.calls[-2][1]
        == "/repos/builacviet/agent-workflow/branches/codex%2Ftest"
    )
    with pytest.raises(WorkflowError):
        verifier(REPOSITORY, "codex/test", BASE, BASE)
    with pytest.raises(WorkflowError):
        verifier("other/repository", "main", BASE, HEAD)


@pytest.mark.parametrize("ids", [(True, 2), (0, 2), (1, -2), ("1", 2)])
def test_invalid_app_ids(app, ids):
    with pytest.raises(WorkflowError):
        GitHubApp(*ids, app[0].pem)


def test_configuration_requires_explicit_scope():
    with pytest.raises(WorkflowError):
        validate_config({"repository": "other/repo", "tasks": {}})


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


def test_malformed_and_expired_installation_tokens(app):
    client, transport, _ = app
    original = transport.__call__

    def broken(method, endpoint, token, payload=None):
        if endpoint.endswith("/access_tokens"):
            return {"token": "synthetic", "expires_at": "1970-01-01T00:00:00Z"}
        return original(method, endpoint, token, payload)

    client.transport = broken
    with pytest.raises(WorkflowError, match="invalid installation"):
        client.token()


def test_app_http_error_never_exposes_token(monkeypatch):
    import agent_workflow.workers.trusted_git.app_auth as module

    def fail(data):
        raise OSError("secret synthetic-token")

    monkeypatch.setattr(module, "_run_http_worker", fail)
    with pytest.raises(WorkflowError) as error:
        module.request_json("GET", "/app", "synthetic-token")
    assert "synthetic-token" not in str(error.value)


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


@pytest.mark.parametrize("repository", [REPOSITORY, LEGACY_REPOSITORY])
def test_valid_config_and_profile_digest(config, repository):
    config["repository"] = repository
    validate_config(config)
    config["seccomp_sha256"] = "0" * 64
    with pytest.raises(WorkflowError, match="digest"):
        validate_config(config)


@pytest.mark.parametrize(
    "key,value",
    [
        ("repository", "other/agent-workflow"),
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
    with pytest.raises(WorkflowError):
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
    with pytest.raises(WorkflowError):
        safe_path(path)


def test_export_rejects_hardlinks(tmp_path):
    import os

    first = tmp_path / "a.py"
    first.write_text("fixture")
    os.link(first, tmp_path / "b.py")
    with pytest.raises(WorkflowError):
        changes(tmp_path, {}, ["a.py", "b.py"])


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
    with pytest.raises(WorkflowError, match="v2 paths"):
        validate_config(sample)


@pytest.mark.parametrize(
    "directory",
    ["agent_workflow_v2", "workflow_console_v2", "workflow-console-v2"],
)
def test_actual_v2_directory_roots_stay_forbidden(tmp_path, directory):
    from agent_workflow.runners.coder_runtime import independent_path

    with pytest.raises(WorkflowError, match="v2 paths"):
        independent_path(tmp_path / directory / "data/workflow.sqlite3")


def test_cleanup_remains_available_after_deadline(monkeypatch, tmp_path):
    import agent_workflow.budget as budget
    import agent_workflow.runners.coder_runtime as runtime

    monkeypatch.setenv("WFC_SANDBOX_STATE_DIR", str(tmp_path / "sandbox-fences"))
    now = [1000]
    monkeypatch.setattr(budget.time, "time", lambda: now[0])

    def command(*args, **kwargs):
        assert budget.operation_timeout(30) == 30
        return b""

    monkeypatch.setattr(runtime, "docker_command", command)
    from agent_workflow.runners import sandbox

    monkeypatch.setattr(sandbox, "_require_engine", lambda: "fixture-daemon-a")
    from unittest.mock import Mock

    from agent_workflow.application.task.use_cases.register_task_handoff import (
        RegisterTaskHandoffUseCase,
    )

    controller_runtime = runtime.CoderRuntime(
        {},
        Mock(spec=RegisterTaskHandoffUseCase),
        dispatch=Mock(),
        ci=Mock(),
        leadership_guard=lambda: True,
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
