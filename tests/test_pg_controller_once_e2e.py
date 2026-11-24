"""Offline PG one-shot admission and lifecycle; all external effects are fake."""

import hashlib
import json
import runpy
import sqlite3
import sys
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine

from agent_workflow.app_config import AppConfig
from agent_workflow.application.exceptions import InvalidApplicationRequestError
from agent_workflow.application.review.dtos import ReviewTask
from agent_workflow.application.review.use_cases.get_review_context import (
    GetReviewContextOutput,
)
from agent_workflow.application.task.use_cases.get_coder_assignment import (
    GetCoderAssignmentUseCase,
)
from agent_workflow.composition import postgres_ci_entrypoint
from agent_workflow.composition import postgres_dispatch_deps as deps_wiring
from agent_workflow.composition import postgres_dispatch_entrypoint as entry
from agent_workflow.composition.postgres_controller import compose_postgres_controller
from agent_workflow.domain.states import WorkflowStatus
from agent_workflow.integrations.github import REPOSITORY, WorkflowError
from agent_workflow.interfaces.cli import controller_cli
from agent_workflow.interfaces.cli import dispatch_controller as dispatch_cli
from agent_workflow.interfaces.review_assignment_response import coder_assignment_response
from agent_workflow.runners import coder_runtime, sandbox
from agent_workflow.storage.postgres_connection import PostgresFactory
from agent_workflow.storage.postgres_models import CITask, Task
from agent_workflow.workers.trusted_git import main as trusted_worker

DSN = "postgresql+psycopg://user:pass@127.0.0.1/workflow_test"
PG_CONTROLLER = "agent_workflow.controller"
INPUT = "b" * 40
HEAD = "c" * 40


def forbidden(*args, **kwargs):
    pytest.fail("unexpected external effect or SQLite fallback")


@pytest.fixture
def offline_factory(monkeypatch):
    monkeypatch.setattr(PostgresFactory, "assert_schema_ready", lambda _: None)
    engine = create_engine("postgresql+psycopg://", creator=forbidden)
    monkeypatch.setattr(engine, "dispose", Mock(wraps=engine.dispose))
    return PostgresFactory(engine)


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


def run_dispatch(config, app_config):
    return dispatch_cli.run_dispatch_pass(
        config, app_config, compose=entry.compose_dispatch_execution
    ).worker_completed


def test_pg_once_cli_routes_without_sqlite(config, tmp_path, monkeypatch, capsys):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    monkeypatch.setenv("PG_RUNTIME_DSN", DSN)
    monkeypatch.setattr(sys, "argv", ["controller", str(path), "--once"])
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    execute = Mock(return_value=False)

    @contextmanager
    def compose(settings, app_config):
        assert settings == config
        assert app_config.pg_runtime_dsn == DSN
        yield SimpleNamespace(execute=execute)

    monkeypatch.setattr(entry, "compose_dispatch_execution", compose)
    runpy.run_module(PG_CONTROLLER, run_name="__main__")
    execute.assert_called_once_with({"T"}, 1800)
    assert capsys.readouterr().out == "PostgreSQL dispatch: no work claimed.\n"


@pytest.mark.parametrize(
    "flags",
    [
        [],
        ["--ci-once", "--ci-watch"],
        ["--once", "--ci-watch"],
        ["--ci-w"],
        ["--acknowledge", "private-dispatch-id", "--reconciled"],
        ["--once", "--acknowledge", "private-dispatch-id"],
        ["--ci-once", "--reconciled"],
    ],
)
def test_pg_only_cli_rejects_unsupported_flags_before_config(
    monkeypatch, capsys, flags
):
    monkeypatch.setattr(sys, "argv", ["controller", "config.json", *flags])
    monkeypatch.setattr(controller_cli, "read_controller_config", forbidden)
    monkeypatch.setattr(entry, "compose_dispatch_execution", forbidden)
    monkeypatch.setattr(postgres_ci_entrypoint, "compose_ci_execution", forbidden)

    with pytest.raises(SystemExit) as error:
        runpy.run_module(PG_CONTROLLER, run_name="__main__")

    assert error.value.code == 2
    output = capsys.readouterr()
    assert not output.out
    assert "exactly one supported mode" in output.err
    assert "private-dispatch-id" not in output.err


@pytest.mark.parametrize("mode", ["--once", "--ci-once", "--ci-watch"])
@pytest.mark.parametrize("dsn", [None, "", "sqlite:///private-dsn.sqlite", DSN])
def test_pg_only_cli_rejects_invalid_dsn_or_stale_config_before_resources(
    config, tmp_path, monkeypatch, capsys, mode, dsn
):
    if dsn is None:
        monkeypatch.delenv("PG_RUNTIME_DSN", raising=False)
    else:
        monkeypatch.setenv("PG_RUNTIME_DSN", dsn)
    sqlite_path = tmp_path / "private-legacy.sqlite"
    if dsn == DSN:
        config["database"] = str(sqlite_path)
    path = tmp_path / "controller.json"
    path.write_text(json.dumps(config))
    monkeypatch.setattr(sys, "argv", ["controller", str(path), mode])
    monkeypatch.setattr(entry, "compose_dispatch_execution", forbidden)
    monkeypatch.setattr(postgres_ci_entrypoint, "compose_ci_execution", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)

    with pytest.raises(SystemExit) as error:
        runpy.run_module(PG_CONTROLLER, run_name="__main__")

    assert error.value.code == (1 if mode == "--once" else 2)
    output = capsys.readouterr()
    assert not output.out
    assert "configuration" in output.err or "PG_RUNTIME_DSN" in output.err
    assert "private-" not in output.err
    assert not sqlite_path.exists()


@pytest.mark.parametrize("dsn", [None, ""])
def test_pg_once_cli_without_dsn_never_calls_legacy(
    config, tmp_path, monkeypatch, capsys, dsn
):
    path = tmp_path / "config.json"
    sqlite_path = tmp_path / "misleading.sqlite"
    if dsn is None:
        monkeypatch.delenv("PG_RUNTIME_DSN", raising=False)
    else:
        config["database"] = str(sqlite_path)
        monkeypatch.setenv("PG_RUNTIME_DSN", dsn)
    path.write_text(json.dumps(config))
    monkeypatch.setattr(sys, "argv", ["controller", str(path), "--once"])
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(entry.PostgresFactory, "open", forbidden)
    with pytest.raises(SystemExit) as error:
        runpy.run_module(PG_CONTROLLER, run_name="__main__")
    assert error.value.code == 1
    assert "PG_RUNTIME_DSN" in capsys.readouterr().err
    assert not sqlite_path.exists()


@pytest.mark.parametrize("dsn", [None, "", "postgresql://bad"])
def test_missing_or_invalid_dsn_before_resources(config, monkeypatch, tmp_path, dsn):
    monkeypatch.delenv("PG_RUNTIME_DSN", raising=False)
    monkeypatch.setattr(entry.PostgresFactory, "open", forbidden)
    monkeypatch.setattr(deps_wiring, "compose_postgres_worker", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(coder_runtime, "SandboxManager", forbidden)
    sqlite_path = tmp_path / "misleading.sqlite"
    app = AppConfig()
    # Bypass model validation to ensure the entrypoint itself rejects it.
    app = app.model_copy(update={"pg_runtime_dsn": dsn})
    with pytest.raises(ValueError, match="PG_RUNTIME_DSN"):
        run_dispatch(config | {"database": str(sqlite_path)}, app)
    assert not sqlite_path.exists()


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
    monkeypatch.setattr(deps_wiring, "compose_postgres_worker", forbidden)
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
    with pytest.raises(InvalidApplicationRequestError):
        run_dispatch(config, AppConfig(pg_runtime_dsn=DSN))


def test_pg_validation_never_inspects_sqlite_database(config):
    config["database"] = object()
    entry._validate_worker_config(config)


def test_entrypoint_rejects_uncomposed_repository_before_leadership(
    config, monkeypatch, offline_factory
):
    dependencies = compose_postgres_controller(offline_factory)
    acquire = Mock()
    claim = Mock()
    monkeypatch.setattr(dependencies.leadership, "try_acquire", acquire)
    monkeypatch.setattr(dependencies.dispatch, "claim", claim)
    monkeypatch.setattr(PostgresFactory, "open", lambda _: offline_factory)
    monkeypatch.setattr(
        deps_wiring,
        "compose_postgres_controller",
        lambda factory: dependencies if factory is offline_factory else forbidden(),
    )
    dependencies = replace(dependencies, workflow=object())

    with pytest.raises(TypeError, match="composed PostgreSQL workflow"):
        run_dispatch(config, AppConfig(pg_runtime_dsn=DSN))

    acquire.assert_not_called()
    claim.assert_not_called()
    assert offline_factory._closed


@pytest.mark.parametrize(
    "outcome",
    [
        "success",
        "empty",
        "contended",
        "wrong_head",
        "wrong_task",
        "wrong_assignment_task",
        "leadership_lost_after_assignment",
        "worker_error",
        "cleanup_error",
    ],
)
def test_single_factory_lazy_worker_and_terminal_order(
    config, monkeypatch, outcome, offline_factory
):
    factory = offline_factory
    deps = compose_postgres_controller(factory)
    events = []
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
    if outcome == "wrong_assignment_task":
        context["task_id"] = "OTHER"
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

    def get_assignment(_service, task_id):
        nonlocal held
        assert held and task_id == claim["task_id"]
        if outcome == "leadership_lost_after_assignment":
            held = False
        return context

    monkeypatch.setattr(
        deps_wiring.GetCoderAssignmentUseCase, "get_coder_assignment", get_assignment
    )
    opened = Mock(return_value=factory)
    monkeypatch.setattr(PostgresFactory, "open", opened)
    monkeypatch.setattr(
        deps_wiring,
        "compose_postgres_controller",
        lambda borrowed: deps if borrowed is factory else forbidden(),
    )
    monkeypatch.setattr(coder_runtime, "_require_engine", forbidden)

    def compose(cfg, service, borrowed, *, leadership_guard):
        assert borrowed is deps and service.scope.repository is deps.workflow
        assert cfg is config
        leadership_guard()
        events.append("compose_worker")

        def execute(actual_claim, actual_context):
            assert actual_claim is claim and actual_context is context
            leadership_guard()
            events.append("worker")
            if outcome == "worker_error":
                raise RuntimeError("fake Worker uncertain")
            return HEAD

        def clear(dispatch_id):
            leadership_guard()
            assert dispatch_id == claim["dispatch_id"] and events[-1] == "finish"
            events.append("clear")
            if outcome == "cleanup_error":
                raise RuntimeError("pin retained")

        return SimpleNamespace(execute_assignment=execute, release_engine=clear)

    worker = Mock(side_effect=compose)
    monkeypatch.setattr(deps_wiring, "compose_postgres_worker", worker)
    monkeypatch.setattr(PostgresFactory, "__init__", forbidden)
    if outcome == "leadership_lost_after_assignment":
        with pytest.raises(PermissionError, match="reconcile in-flight work"):
            run_dispatch(config, AppConfig(pg_runtime_dsn=DSN))
    elif outcome in {
        "wrong_head",
        "wrong_task",
        "wrong_assignment_task",
        "worker_error",
        "cleanup_error",
    }:
        with pytest.raises((WorkflowError, RuntimeError)):
            run_dispatch(config, AppConfig(pg_runtime_dsn=DSN))
    else:
        assert run_dispatch(config, AppConfig(pg_runtime_dsn=DSN)) == (
            outcome == "success"
        )
    opened.assert_called_once()
    assert factory._closed
    factory._engine.dispose.assert_called_once()
    assert events[-1] == "release"
    if outcome == "success":
        assert events == ["compose_worker", "worker", "finish", "clear", "release"]
    if outcome in {
        "empty",
        "contended",
        "wrong_head",
        "wrong_task",
        "wrong_assignment_task",
        "leadership_lost_after_assignment",
    }:
        worker.assert_not_called()
    if outcome in {"wrong_head", "wrong_task", "wrong_assignment_task"}:
        assert events == ["block", "release"]
    if outcome == "leadership_lost_after_assignment":
        assert events == ["release"]
    if outcome == "worker_error":
        assert events == ["compose_worker", "worker", "block", "release"]
    if outcome == "cleanup_error":
        assert events == ["compose_worker", "worker", "finish", "clear", "release"]
    if outcome != "contended":
        claim_mock.assert_called_once_with({"T"}, 1800, track_pre_launch=True)


@pytest.mark.parametrize(
    ("fault", "error", "expected_stages"),
    [
        (None, None, ["prepare", "code", "test", "publish"]),
        ("mutated_test", "test phase mutated source", ["prepare", "code", "test"]),
        (
            "outside_allowlist",
            "outside the allowlist",
            ["prepare", "code", "test", "publish"],
        ),
        (
            "invalid_result_head",
            "40-character commit SHA",
            ["prepare", "code", "test", "publish"],
        ),
        (
            "publish_cleanup",
            "cleanup unconfirmed",
            ["prepare", "code", "test", "publish"],
        ),
    ],
)
def test_pg_once_runs_native_dev_stages(
    config, tmp_path, monkeypatch, fault, error, expected_stages, offline_factory
):
    # Only local Git runs. PostgreSQL I/O, Docker transport, Codex and GitHub
    # are synthetic while the Controller, Worker and phase coordinator run intact.
    for name in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
        monkeypatch.setenv(name, "2026-01-01T00:00:00+00:00")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "unused-git-config"))
    git = trusted_worker.git
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    branch = "codex/fixture"
    git(upstream, "init", "-b", branch)
    (upstream / "src").mkdir()
    (upstream / "src" / "value.txt").write_bytes(b"before\n")
    git(upstream, "add", "src")
    git(
        upstream,
        "-c",
        "user.name=fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        "fixture base",
    )
    base_sha = git(upstream, "rev-parse", "HEAD").decode().strip()
    handoff_path = "src/handoff.md"
    config["tasks"]["T"]["handoff_path"] = handoff_path
    dispatch_id = "a" * 32
    engine_pin = tmp_path / "fences" / f"{dispatch_id}.engine"
    stage = (
        Path(config["tasks"]["T"]["worktree"]) / ".runtime" / "dispatch" / dispatch_id
    )
    claim = {
        "dispatch_id": dispatch_id,
        "task_id": "T",
        "input_sha": base_sha,
        "round_number": 0,
        "deadline": coder_runtime.time.time() + 120,
    }
    context_paths = (handoff_path, "src/value.txt")
    snapshot = GetReviewContextOutput(
        task=ReviewTask(
            task_id="T",
            repository=REPOSITORY,
            branch=branch,
            base_sha=base_sha,
            current_head_sha=None,
            requested_effort="medium",
            require_verified_effort=0,
            max_review_rounds=2,
            completed_review_rounds=0,
            status=WorkflowStatus.AWAITING_HANDOFF,
            created_at=1.0,
            updated_at=1.0,
            context_paths=context_paths,
            spec_json=json.dumps({"context_paths": context_paths}),
        ),
        review_round=1,
        rounds_remaining=2,
        previous_reviewed_sha=None,
        previous_verdict=None,
        previous_reviews=(),
        events=(),
    )
    reader = Mock()
    reader.execute.return_value = snapshot
    projection = GetCoderAssignmentUseCase(reader).get_coder_assignment("T")
    assignment = coder_assignment_response(projection["details"])

    factory = offline_factory
    deps = compose_postgres_controller(factory)
    monkeypatch.setattr(PostgresFactory, "open", Mock(return_value=factory))
    monkeypatch.setattr(
        deps_wiring, "compose_postgres_controller", Mock(return_value=deps)
    )
    claim_task = Mock(return_value=claim)
    events = Mock()
    held = True

    def release():
        nonlocal held
        assert engine_pin.exists() == bool(error)
        held = False
        events.release()

    def finish(finished_id, sha):
        assert engine_pin.is_file() and held
        events.finish(finished_id, sha)

    monkeypatch.setattr(deps.leadership, "try_acquire", lambda: held)
    monkeypatch.setattr(deps.leadership, "is_held", lambda: held)
    monkeypatch.setattr(deps.leadership, "release", release)
    monkeypatch.setattr(deps.dispatch, "claim", claim_task)
    monkeypatch.setattr(deps.dispatch, "retire_pre_launch", events.retire)
    monkeypatch.setattr(deps.dispatch, "record_runtime_evidence", events.evidence)
    monkeypatch.setattr(deps.dispatch, "finish", finish)
    monkeypatch.setattr(deps.dispatch, "block", events.block)
    get_assignment = Mock(return_value=projection)
    monkeypatch.setattr(
        deps_wiring.GetCoderAssignmentUseCase, "get_coder_assignment", get_assignment
    )
    monkeypatch.setattr(
        deps_wiring.RegisterTaskHandoffUseCase,
        "execute",
        lambda _, request: events.handoff(request),
    )

    session = Mock()
    session.begin.return_value = nullcontext()
    session.add.side_effect = events.track

    def get_row(model, task_id, *, with_for_update):
        assert task_id == "T" and with_for_update
        if model is Task:
            return SimpleNamespace(
                repository=REPOSITORY,
                current_head_sha=events.handoff.call_args.args[0].new_head_sha,
                status="READY_IN_REVIEW",
            )
        assert model is CITask
        return None

    session.get.side_effect = get_row
    monkeypatch.setattr(factory, "get_scoped_session", lambda: nullcontext(session))
    for module in (coder_runtime, sandbox):
        monkeypatch.setattr(module, "_selected_host", lambda: "unix:///fixture.sock")
        monkeypatch.setattr(module, "_require_engine", lambda: "fixture-daemon")
    monkeypatch.setattr(trusted_worker, "ROOT", stage)
    monkeypatch.setattr(trusted_worker, "GitHubApp", forbidden)
    verifier = Mock()
    monkeypatch.setattr(trusted_worker, "AppVerifier", Mock(return_value=verifier))
    github = Mock(spec=["api", "identity"])
    github.identity.return_value = ("fixture", "fixture@example.invalid")
    pr_url = f"https://github.com/{REPOSITORY}/pull/7"

    def github_api(method, endpoint, payload=None):
        prefix = f"/repos/{REPOSITORY}/pulls"
        if method == "GET":
            assert endpoint.startswith(prefix + "?") and payload is None
            return []
        assert method == "POST" and endpoint == prefix
        assert (
            payload["title"],
            payload["base"],
            payload["head"],
            payload["draft"],
        ) == ("T", "main", branch, True)
        assert pushed_sha in payload["body"]
        events.draft()
        return {
            "draft": True,
            "user": {"login": trusted_worker.BOT},
            "html_url": pr_url,
        }

    github.api.side_effect = github_api
    pushed_sha = None

    def offline_git_auth(app, repo, operation, *args):
        nonlocal pushed_sha
        assert app is github and repo == stage / "repo" and held
        if operation == "fetch":
            assert args == ("--no-tags", "origin", f"refs/heads/{branch}")
            return git(repo, "fetch", "--no-tags", str(upstream), args[-1])
        assert operation == "push" and args == ("origin", f"HEAD:refs/heads/{branch}")
        assert git(repo, "show", "HEAD:src/value.txt") == b"after\n"
        pushed_sha = git(repo, "rev-parse", "HEAD").decode().strip()
        events.push()
        return b""

    monkeypatch.setattr(trusted_worker, "git_auth", offline_git_auth)
    stage_calls = []
    tested_digest = None
    volume = config["codex_auth_volume"]

    def fake_docker_command(args):
        assert held and engine_pin.is_file()
        if args[:3] == ["docker", "volume", "inspect"]:
            assert args == ["docker", "volume", "inspect", volume]
        else:
            assert args[:-1] == ["docker", "container", "ls", "-aq", "--filter"]
            assert args[-1].startswith(f"name=^/my-wfc-{dispatch_id}-")
            if fault == "publish_cleanup" and stage_calls[-1] == "publish":
                raise WorkflowError("cleanup unconfirmed")
        return b""

    def run_stage(args, prompt, remaining):
        nonlocal tested_digest
        mode = args[-1]
        stage_calls.append(mode)
        assert held and remaining > 0
        assert args[-2] == config["image"]
        mounts = [args[i + 1] for i, arg in enumerate(args) if arg == "--mount"]
        source = stage / "source"
        if mode in {"prepare", "publish"}:
            assert mounts == [
                f"type=bind,src={stage},dst=/workspace",
                f"type=bind,src={config['pem']},dst=/run/.secret/coder-app.pem,readonly",
            ]
            envelope = json.loads(prompt)
            assert envelope["context"] == assignment
            assert envelope["input_sha"] == base_sha
            assert envelope["write_paths"] == ["src"]
            assert envelope["handoff_path"] == handoff_path
            if mode == "prepare":
                trusted_worker.prepare(envelope, github)
            else:
                assert coder_runtime.source_digest(source) == tested_digest
                trusted_worker.publish(envelope, github)
                if fault == "invalid_result_head":
                    receipt = stage / "result.json"
                    receipt.write_text(
                        json.dumps(
                            json.loads(receipt.read_text()) | {"head_sha": "invalid"}
                        )
                    )
        elif mode == "code":
            assert mounts == [
                f"type=bind,src={source},dst=/workspace",
                f"type=volume,src={volume},dst=/home/coder/.codex",
            ]
            assert f"WFC_CODEX_MODEL={config['model']}" in args
            assert json.loads(prompt.split("\n", 1)[1]) == assignment
            assert not any(
                path.name in {".git", ".secret", ".codex"} for path in source.rglob("*")
            )
            github.api.assert_not_called()
            (source / "src" / "value.txt").write_bytes(b"after\n")
            if fault == "outside_allowlist":
                (source / "outside.txt").write_bytes(b"unexpected\n")
        else:
            assert mode == "test" and "--network=none" in args
            assert mounts == [f"type=bind,src={source},dst=/workspace"]
            assert (source / "src" / "value.txt").read_bytes() == b"after\n"
            tested_digest = coder_runtime.source_digest(source)
            if fault == "mutated_test":
                (source / "src" / "value.txt").write_bytes(b"mutated\n")
        return b""

    monkeypatch.setattr(coder_runtime, "docker_command", fake_docker_command)
    monkeypatch.setattr(coder_runtime, "run_container", run_stage)
    app_config = AppConfig(pg_runtime_dsn=DSN)
    if error:
        with pytest.raises(WorkflowError, match=error):
            run_dispatch(config, app_config)
        assert [call[0] for call in events.mock_calls] == [
            "retire",
            *(
                ["push", "draft"]
                if fault in {"invalid_result_head", "publish_cleanup"}
                else []
            ),
            "block",
            "release",
        ]
        events.block.assert_called_once_with(dispatch_id)
    else:
        config_file = tmp_path / "controller.json"
        config_file.write_text(json.dumps(config))
        monkeypatch.setenv("PG_RUNTIME_DSN", DSN)
        monkeypatch.setattr(sys, "argv", ["controller", str(config_file), "--once"])
        runpy.run_module(PG_CONTROLLER, run_name="__main__")
        assert [call[0] for call in events.mock_calls] == [
            "retire",
            "push",
            "draft",
            "evidence",
            "handoff",
            "track",
            "finish",
            "release",
        ]
        assert pushed_sha and pushed_sha != base_sha
        assert [call.args for call in verifier.call_args_list] == [
            (REPOSITORY, branch, base_sha, base_sha),
            (REPOSITORY, branch, base_sha, base_sha),
            (REPOSITORY, branch, base_sha, pushed_sha),
        ]
        assert events.evidence.call_args.args[:4] == (
            dispatch_id,
            "T",
            base_sha,
            "codex",
        )
        assert events.evidence.call_args.args[-4:] == (
            None,
            "UNVERIFIED",
            tested_digest,
            pushed_sha,
        )
        request = events.handoff.call_args.args[0]
        assert (
            request.task_id,
            request.repository,
            request.branch,
            request.base_sha,
            request.new_head_sha,
            request.handoff_file_path,
        ) == (
            "T",
            REPOSITORY,
            branch,
            base_sha,
            pushed_sha,
            handoff_path,
        )
        binding = events.track.call_args.args[0]
        assert (
            binding.task_id,
            binding.pr_number,
            binding.head_sha,
            binding.verdict,
        ) == ("T", 7, pushed_sha, "BLOCKED")
        events.finish.assert_called_once_with(dispatch_id, pushed_sha)

    assert stage_calls == expected_stages
    assert factory._closed
    claim_task.assert_called_once_with({"T"}, 1800, track_pre_launch=True)
    get_assignment.assert_called_once_with("T")
