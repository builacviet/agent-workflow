"""Offline PG one-shot admission and lifecycle; all external effects are fake."""

import hashlib
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy.engine import Engine

from agent_workflow.app_config import AppConfig
from agent_workflow.composition import postgres_dispatch_entrypoint as entry
from agent_workflow.composition.postgres_controller import compose_postgres_controller
from agent_workflow.integrations.github import REPOSITORY, WorkflowError
from agent_workflow.orchestration import controller
from agent_workflow.runners import coder_runtime
from agent_workflow.storage.postgres_connection import PostgresFactory

DSN = "postgresql+psycopg://user:pass@127.0.0.1/workflow_test"
INPUT = "b" * 40
HEAD = "c" * 40


def forbidden(*args, **kwargs):
    pytest.fail("unexpected external effect or SQLite fallback")


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("WFC_SANDBOX_STATE_DIR", str(tmp_path / "fences"))
    secret = tmp_path / ".secret"
    secret.mkdir()
    for name in ("worker", "controller"):
        (secret / f"{name}.pem").write_text("synthetic unused key")
    root = tmp_path / "worktree"
    root.mkdir()
    (root / ".git").write_text("synthetic linked worktree marker")
    profile = tmp_path / "seccomp.json"
    profile.write_text("{}")
    return {
        "repository": REPOSITORY,
        "tasks": {"T": {"worktree": str(root), "write_paths": ["src"]}},
        "pem": str(secret / "worker.pem"),
        "app_id": 1,
        "installation_id": 2,
        "controller_github_app": {
            "app_id": 3,
            "installation_id": 4,
            "pem": str(secret / "controller.pem"),
        },
        "scratch": str(tmp_path / "scratch"),
        "seccomp_profile": str(profile),
        "seccomp_sha256": hashlib.sha256(b"{}").hexdigest(),
        "codex_auth_volume": "techlead-review-poc_codex_auth",
        "image": "fixture-image",
        "model": "gpt-6-luna",
    }


def test_pg_once_cli_routes_without_sqlite(config, tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    monkeypatch.setenv("PG_RUNTIME_DSN", DSN)
    monkeypatch.setattr(sys, "argv", ["controller", str(path), "--once"])
    monkeypatch.setattr(controller, "process_lock", forbidden)
    monkeypatch.setattr(controller, "operate", forbidden)
    runner = Mock()
    monkeypatch.setattr(entry, "run_postgres_dispatch_once", runner)
    controller.main()
    assert runner.call_args.args[0] == config
    assert runner.call_args.args[1].pg_runtime_dsn == DSN


@pytest.mark.parametrize("dsn", [None, "", "postgresql://bad"])
def test_missing_or_invalid_dsn_before_resources(config, monkeypatch, dsn):
    monkeypatch.setattr(entry.PostgresFactory, "open", forbidden)
    monkeypatch.setattr(entry, "compose_postgres_worker", forbidden)
    app = AppConfig()
    # Bypass model validation to ensure the entrypoint itself rejects it.
    app = app.model_copy(update={"pg_runtime_dsn": dsn})
    with pytest.raises(ValueError, match="PG_RUNTIME_DSN"):
        entry.run_postgres_dispatch_once(config, app)


@pytest.mark.parametrize(
    "unsafe",
    [
        "tasks",
        "app_id",
        "pem",
        "same_key",
        "same_app",
        "same_installation",
        "seccomp",
        "timeout",
        "write_paths",
        "worktree",
        "image",
        "runtime",
        "model",
    ],
)
def test_unsafe_config_before_resources(config, monkeypatch, unsafe):
    monkeypatch.setattr(entry.PostgresFactory, "open", forbidden)
    monkeypatch.setattr(entry, "compose_postgres_worker", forbidden)
    if unsafe == "tasks":
        config["tasks"] = ["T"]
    elif unsafe == "app_id":
        config["app_id"] = True
    elif unsafe == "pem":
        config["pem"] = "relative.pem"
    elif unsafe == "same_key":
        config["controller_github_app"]["pem"] = config["pem"]
    elif unsafe == "same_app":
        config["controller_github_app"]["app_id"] = config["app_id"]
    elif unsafe == "same_installation":
        config["controller_github_app"]["installation_id"] = config["installation_id"]
    elif unsafe == "seccomp":
        config["seccomp_sha256"] = "0" * 64
    elif unsafe == "timeout":
        config["timeout_seconds"] = 1
    elif unsafe == "write_paths":
        config["tasks"]["T"]["write_paths"] = [".secret"]
    elif unsafe == "worktree":
        config["tasks"]["T"]["worktree"] = "relative"
    elif unsafe == "image":
        config["image"] = "unsafe image"
    elif unsafe == "runtime":
        config["runtime"] = "unregistered"
    elif unsafe == "model":
        config["model"] = "unapproved"
    with pytest.raises(WorkflowError, match="configuration"):
        entry.run_postgres_dispatch_once(config, AppConfig(pg_runtime_dsn=DSN))


def test_pg_validation_never_inspects_sqlite_database(config):
    config["database"] = object()
    entry._validate(config, AppConfig(pg_runtime_dsn=DSN))


@pytest.mark.parametrize(
    "outcome",
    [
        "success",
        "empty",
        "contended",
        "wrong_head",
        "wrong_task",
        "worker_error",
        "cleanup_error",
    ],
)
def test_single_factory_lazy_worker_and_terminal_order(config, monkeypatch, outcome):
    monkeypatch.setattr(PostgresFactory, "assert_schema_ready", lambda _: None)
    factory = PostgresFactory(Mock(spec=Engine))
    composition = compose_postgres_controller(factory)
    deps = composition.controller
    events = []
    stages = []
    evidence = Mock()
    ci_binding = {}
    original_worker = entry.compose_postgres_worker
    held = True
    claim = {
        "dispatch_id": "a" * 32,
        "task_id": "T",
        "input_sha": INPUT,
        "round_number": 0,
        "deadline": 9999999999.0,
    }
    context = {
        "task_id": "T",
        "status": "AWAITING_HANDOFF",
        "base_sha": INPUT,
        "current_head_sha": None,
        "completed_review_rounds": 0,
    }
    if outcome == "wrong_head":
        context["base_sha"] = HEAD
    if outcome == "wrong_task":
        claim["task_id"] = context["task_id"] = "OTHER"
    monkeypatch.setattr(deps.leadership, "try_acquire", lambda: outcome != "contended")
    monkeypatch.setattr(deps.leadership, "is_held", lambda: held)

    def release():
        nonlocal held
        events.append("release")
        held = False

    monkeypatch.setattr(deps.leadership, "release", release)
    claim_mock = Mock(return_value=None if outcome == "empty" else claim)
    monkeypatch.setattr(deps.dispatch, "claim", claim_mock)
    monkeypatch.setattr(deps.dispatch, "finish", lambda *args: events.append("finish"))
    monkeypatch.setattr(deps.dispatch, "block", lambda *args: events.append("block"))
    monkeypatch.setattr(
        entry.WorkflowService, "get_coder_assignment", lambda *_: context
    )
    opened = Mock(return_value=factory)
    monkeypatch.setattr(PostgresFactory, "open", opened)
    monkeypatch.setattr(
        entry,
        "compose_postgres_controller",
        lambda borrowed: composition if borrowed is factory else forbidden(),
    )
    monkeypatch.setattr(coder_runtime, "DispatchLedger", forbidden)
    monkeypatch.setattr(coder_runtime, "_SqliteCiTracking", forbidden)
    monkeypatch.setattr(coder_runtime, "_require_engine", forbidden)

    def compose(cfg, service, borrowed, *, leadership_guard):
        assert borrowed is deps and service.repository is deps.workflow
        assert cfg is config
        leadership_guard()
        events.append("compose_worker")
        if outcome == "success":
            from test_agent_runtime import FakeHost

            runtime = original_worker(
                cfg, service, borrowed, leadership_guard=leadership_guard
            )
            host = FakeHost(stages)
            runtime.agent_runtime.host = host
            monkeypatch.setattr(deps.dispatch, "record_runtime_evidence", evidence)

            def track(task_id, number, head):
                assert stages[-1] == "handoff"
                evidence.assert_called_once()
                ci_binding.update(
                    task_id=task_id, number=number, head=head, verdict="BLOCKED"
                )

            monkeypatch.setattr(deps.ci_state, "track", track)

        def execute(actual_claim, actual_context):
            assert actual_claim is claim and actual_context is context
            leadership_guard()
            events.append("worker")
            if outcome == "worker_error":
                raise RuntimeError("fake Worker uncertain")
            if outcome == "success":
                # Run the actual injected coordinator against a fake host and
                # state ports. No Docker/CLI/GitHub operation can be reached.
                deps.dispatch.retire_pre_launch = Mock()
                deps.dispatch.retire_pre_launch(claim["dispatch_id"])
                return runtime.agent_runtime.execute_bound(
                    claim,
                    context,
                    SimpleNamespace(spec=SimpleNamespace(runtime="fake")),
                    SimpleNamespace(),
                    None,
                )
            return HEAD

        def clear(dispatch_id):
            leadership_guard()
            assert dispatch_id == claim["dispatch_id"] and events[-1] == "finish"
            events.append("clear")
            if outcome == "cleanup_error":
                raise RuntimeError("pin retained")

        return SimpleNamespace(execute=execute, release_engine=clear)

    worker = Mock(side_effect=compose)
    monkeypatch.setattr(entry, "compose_postgres_worker", worker)
    monkeypatch.setattr(PostgresFactory, "__init__", forbidden)
    if outcome in {"wrong_head", "wrong_task", "worker_error", "cleanup_error"}:
        with pytest.raises((WorkflowError, RuntimeError)):
            entry.run_postgres_dispatch_once(config, AppConfig(pg_runtime_dsn=DSN))
    else:
        assert entry.run_postgres_dispatch_once(
            config, AppConfig(pg_runtime_dsn=DSN)
        ) == (outcome == "success")
    opened.assert_called_once()
    assert factory._closed
    factory._engine.dispose.assert_called_once()
    assert events[-1] == "release"
    if outcome == "success":
        assert events == ["compose_worker", "worker", "finish", "clear", "release"]
        evidence.assert_called_once_with(
            claim["dispatch_id"],
            "T",
            INPUT,
            "fake",
            "1",
            None,
            "UNVERIFIED",
            "d" * 64,
            HEAD,
        )
        assert ci_binding == {
            "task_id": "T",
            "number": 7,
            "head": HEAD,
            "verdict": "BLOCKED",
        }
        assert stages.count("publish") == 1
    if outcome in {"empty", "contended", "wrong_head", "wrong_task"}:
        worker.assert_not_called()
    if outcome == "cleanup_error":
        assert "block" not in events
    if outcome != "contended":
        claim_mock.assert_called_once_with({"T"}, 1800, track_pre_launch=True)
