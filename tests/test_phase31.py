"""Deterministic Phase 3.1 boundaries; no external credentials or model calls."""

import base64
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from coder_worker.app_auth import AppVerifier, BOT, GitHubApp, REPOSITORY
from agent_workflow.coder_runtime import safe_path, validate_config
from coder_worker.repository import changes
from agent_workflow.controller import DispatchLedger, run_once
from agent_workflow.github import LEGACY_REPOSITORY, WorkflowError
from agent_workflow.service import WorkflowService, encode

BASE = "a" * 40
HEAD = "b" * 40


def provision(service, task_id="P31"):
    service.provision_task(
        dict(
            task_id=task_id,
            title="Controller fixture",
            description="Deterministic only",
            acceptance_criteria=["AC1"],
            definition_of_done="tests",
            repository=REPOSITORY,
            branch="codex/test",
            base_sha=BASE,
            requested_effort="high",
            risk_level="high",
            require_verified_effort=True,
            context_paths=["docs/handoff.md"],
            synthetic=True,
        )
    )


@pytest.fixture
def state(tmp_path):
    service = WorkflowService(tmp_path / "workflow.sqlite3")
    provision(service)
    return service


def test_atomic_global_slot_across_connections(state):
    provision(state, "OTHER")

    def claim(_):
        service = WorkflowService(state.db_path)
        return DispatchLedger(service).claim({"P31", "OTHER"}, 60)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(claim, range(8)))
    assert sum(result is not None for result in results) == 1
    assert state.get_coder_assignment("P31")["completed_review_rounds"] == 0


def test_expiry_restart_and_failure_never_auto_redispatch(state):
    ledger = DispatchLedger(state, clock=lambda: 100)
    claim = ledger.claim({"P31"}, 60)
    restarted = DispatchLedger(state, clock=lambda: 99999)
    assert restarted.claim({"P31"}, 60) is None
    restarted.block(claim["dispatch_id"])
    assert restarted.claim({"P31"}, 60) is None
    restarted.acknowledge(claim["dispatch_id"])
    # Releasing a safety slot does not clear the idempotency identity.
    assert restarted.claim({"P31"}, 60) is None
    assert state.get_coder_assignment("P31")["status"] == "AWAITING_HANDOFF"


def test_success_handoff_and_idempotency(state):
    ledger = DispatchLedger(state)

    def execute(claim, context):
        state.register_task_handoff(
            "P31",
            REPOSITORY,
            "codex/test",
            BASE,
            HEAD,
            "docs/handoff.md",
            "synthetic tests",
        )
        return HEAD

    assert run_once(state, ledger, {"P31"}, execute)
    assert not run_once(state, ledger, {"P31"}, execute)
    assert state.get_review_context("P31")["status"] == "READY_IN_REVIEW"
    assert state.get_review_context("P31")["completed_review_rounds"] == 0


def test_pre_rename_task_can_dispatch_and_handoff_without_rewriting_repository(state):
    with state.repository.read() as db:
        spec = json.loads(state.task(db, "P31")["metadata"])
    legacy_spec = spec | {"repository": LEGACY_REPOSITORY}
    with state.repository.raw_transaction() as db:
        db.execute(
            "UPDATE tasks SET repository=?, metadata=? WHERE task_id=?",
            (LEGACY_REPOSITORY, encode(legacy_spec), "P31"),
        )

    state.provision_task(spec)

    def execute(claim, context):
        assert context["repository"] == REPOSITORY
        state.register_task_handoff(
            "P31", LEGACY_REPOSITORY, "codex/test", BASE, HEAD,
            "docs/handoff.md", "synthetic tests",
        )
        return HEAD

    assert run_once(state, DispatchLedger(state), {"P31"}, execute)
    assert state.list_review_queue()[0]["repository"] == REPOSITORY
    context = state.get_review_context("P31")
    assert context["github_references"]["complete_diff"] == (
        f"https://github.com/{REPOSITORY}/compare/{BASE}...{HEAD}"
    )
    with state.repository.read() as db:
        task = state.task(db, "P31")
    assert task["repository"] == LEGACY_REPOSITORY
    assert task["metadata"] == encode(legacy_spec)


def test_runtime_exception_blocks_slot_and_sanitizes_ledger(state):
    def fail(*args):
        raise RuntimeError("sensitive diagnostic")

    with pytest.raises(RuntimeError):
        run_once(state, DispatchLedger(state), {"P31"}, fail)
    with state.repository.raw_connection() as db:
        row = dict(db.execute("SELECT * FROM coder_dispatches").fetchone())
    assert row["status"] == "BLOCKED" and row["slot"] == 1
    assert "sensitive" not in row["detail"]


@pytest.mark.parametrize(
    "status,rounds",
    [
        ("APPROVED", 1),
        ("ESCALATED", 2),
        ("READY_IN_REVIEW", 0),
        ("IN_REVIEW", 0),
        ("CHANGES_REQUESTED", 2),
    ],
)
def test_review_and_terminal_states_never_dispatch(state, status, rounds):
    with state.repository.raw_transaction() as db:
        db.execute(
            "UPDATE tasks SET status=?,completed_review_rounds=?",
            (status, rounds),
        )
    assert DispatchLedger(state).claim({"P31"}, 60) is None


def test_allowlist_and_round_key(state):
    ledger = DispatchLedger(state)
    assert ledger.claim({"UNKNOWN"}, 60) is None
    first = ledger.claim({"P31"}, 60)
    ledger.finish(first["dispatch_id"], HEAD)
    with state.repository.raw_transaction() as db:
        db.execute(
            "UPDATE tasks SET status='CHANGES_REQUESTED',completed_review_rounds=1,current_head_sha=?",
            (HEAD,),
        )
    second = ledger.claim({"P31"}, 60)
    assert second["input_sha"] == HEAD and second["round_number"] == 1
    assert second["dispatch_id"] != first["dispatch_id"]


@pytest.mark.parametrize("value", [True, 0, 59, 3601, 1.5])
def test_invalid_timeout(state, value):
    with pytest.raises(WorkflowError):
        DispatchLedger(state).claim({"P31"}, value)


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
    claims = json.loads(
        base64.urlsafe_b64decode(client.jwt().split(".")[1] + "==")
    )
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
        sum(
            endpoint.endswith("/access_tokens")
            for _, endpoint, _ in transport.calls
        )
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
    assert transport.calls[-2][1] == "/repos/builacviet/agent-workflow/branches/codex%2Ftest"
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
    from coder_worker.repository import snapshot

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


def test_controller_process_lock_excludes_reconciliation(tmp_path):
    from agent_workflow.controller import process_lock

    lock = tmp_path / "controller.lock"
    with process_lock(lock):
        with pytest.raises(WorkflowError):
            with process_lock(lock):
                pytest.fail("second controller acquired lock")
    with process_lock(lock):
        pass


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
    import coder_worker.app_auth as module

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
    sample = json.loads(
        (root / ".runtime/controller.json.example").read_text()
    )
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
    sample["database"] = str(
        tmp_path / "agent_workflow_v2/data/workflow.sqlite3"
    )
    with pytest.raises(WorkflowError, match="v2 paths"):
        validate_config(sample)


@pytest.mark.parametrize(
    "directory",
    ["agent_workflow_v2", "workflow_console_v2", "workflow-console-v2"],
)
def test_actual_v2_directory_roots_stay_forbidden(tmp_path, directory):
    from agent_workflow.coder_runtime import independent_path

    with pytest.raises(WorkflowError, match="v2 paths"):
        independent_path(tmp_path / directory / "data/workflow.sqlite3")


def test_cleanup_remains_available_after_deadline(monkeypatch, state):
    import agent_workflow.budget as budget
    import agent_workflow.coder_runtime as runtime

    now = [1000]
    monkeypatch.setattr(budget.time, "time", lambda: now[0])

    def command(*args, **kwargs):
        assert budget.operation_timeout(30) == 30
        return b""

    monkeypatch.setattr(runtime, "docker_command", command)
    with budget.dispatch_budget(1001):
        now[0] = 1002
        runtime.CoderRuntime({}, state).stop("a" * 32)
        with pytest.raises(TimeoutError):
            budget.check_deadline()


def test_expired_completion_never_marks_dispatch_done(state, monkeypatch):
    import agent_workflow.budget as budget

    now = [1000]
    monkeypatch.setattr(budget.time, "time", lambda: now[0])

    def execute(claim, context):
        now[0] = 1061
        return HEAD

    with pytest.raises(TimeoutError):
        run_once(
            state,
            DispatchLedger(state, clock=lambda: now[0]),
            {"P31"},
            execute,
            timeout=60,
        )
    with state.repository.raw_connection() as db:
        row = dict(db.execute("SELECT * FROM coder_dispatches").fetchone())
    assert row["status"] == "BLOCKED" and row["slot"] == 1
    assert row["result_sha"] is None


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
