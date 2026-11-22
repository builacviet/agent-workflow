"""Offline Worker composition and admission tests for the PostgreSQL runtime."""

from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine

from agent_workflow.composition.postgres_controller import compose_postgres_controller
from agent_workflow.composition.postgres_leader_runner import run_controller_once
from agent_workflow.composition.postgres_worker import compose_postgres_worker
from agent_workflow.domain.service import WorkflowService
from agent_workflow.integrations.github import REPOSITORY, WorkflowError
from agent_workflow.runners import coder_runtime
from agent_workflow.storage.postgres_connection import PostgresFactory

DISPATCH = "a" * 32
INPUT = "b" * 40
HEAD = "c" * 40
CLAIM = {"dispatch_id": DISPATCH, "task_id": "T", "input_sha": INPUT, "round_number": 0}


@pytest.fixture
def wiring(monkeypatch, tmp_path):
    monkeypatch.delenv("PG_RUNTIME_DSN", raising=False)
    monkeypatch.setenv("WFC_SANDBOX_STATE_DIR", str(tmp_path / "fences"))
    monkeypatch.setattr(PostgresFactory, "assert_schema_ready", lambda self: None)
    factory = PostgresFactory(create_engine("postgresql+psycopg://", creator=forbidden))
    dependencies = compose_postgres_controller(factory).controller
    service = WorkflowService(repository=dependencies.workflow)
    return factory, dependencies, service


def forbidden(*args, **kwargs):
    pytest.fail("unexpected external operation or legacy SQLite path")


def test_borrows_exact_dependencies_without_sqlite_or_external_work(
    wiring, monkeypatch
):
    factory, dependencies, service = wiring
    monkeypatch.setattr(coder_runtime, "DispatchLedger", forbidden)
    monkeypatch.setattr(coder_runtime, "_SqliteCiTracking", forbidden)
    monkeypatch.setattr(coder_runtime.CoderRuntime, "_check_legacy_fences", forbidden)
    monkeypatch.setattr(coder_runtime, "_require_engine", forbidden)
    monkeypatch.setattr(PostgresFactory, "__init__", forbidden)
    monkeypatch.setattr(factory, "connect_session", forbidden)
    connect = Mock(side_effect=forbidden)
    dispose = Mock(side_effect=forbidden)
    monkeypatch.setattr(factory._engine, "connect", connect)
    monkeypatch.setattr(factory._engine, "dispose", dispose)
    monkeypatch.setattr(dependencies.leadership, "is_held", forbidden)

    runtime = compose_postgres_worker(
        {"database": "legacy.db"}, service, dependencies, leadership_guard=forbidden
    )

    assert runtime.service.repository is dependencies.workflow
    assert runtime.agent_runtime.dispatch is dependencies.dispatch
    assert runtime.agent_runtime.ci is dependencies.ci_state.tracker
    assert runtime.agent_runtime.require_ci_tracking is True
    connect.assert_not_called()
    dispose.assert_not_called()


@pytest.mark.parametrize("missing", ["workflow", "dispatch", "ci_state", "leadership"])
def test_missing_or_wrong_controller_dependency_fails_closed(
    wiring, monkeypatch, missing
):
    _, dependencies, service = wiring
    monkeypatch.setattr(coder_runtime, "SandboxManager", forbidden)
    for invalid in (None, object()):
        with pytest.raises(TypeError, match="Worker requires"):
            compose_postgres_worker(
                {},
                service,
                replace(dependencies, **{missing: invalid}),
                leadership_guard=forbidden,
            )


@pytest.mark.parametrize("foreign", ["dispatch", "ci_state", "leadership"])
def test_mixed_factories_are_rejected(wiring, foreign):
    _, dependencies, service = wiring
    other = compose_postgres_controller(
        PostgresFactory(create_engine("postgresql+psycopg://", creator=forbidden))
    ).controller
    with pytest.raises(TypeError, match="share the open Controller factory"):
        compose_postgres_worker(
            {},
            service,
            replace(dependencies, **{foreign: getattr(other, foreign)}),
            leadership_guard=forbidden,
        )


def test_mismatched_service_wrong_lock_or_closed_factory_rejected(wiring):
    factory, dependencies, service = wiring
    other = compose_postgres_controller(factory)
    unrelated_service = WorkflowService(repository=other.controller.workflow)
    with pytest.raises(TypeError, match="composed PostgreSQL workflow"):
        compose_postgres_worker(
            {}, unrelated_service, dependencies, leadership_guard=forbidden
        )
    with pytest.raises(TypeError, match="Controller guard"):
        compose_postgres_worker(
            {},
            service,
            replace(dependencies, leadership=other.ci_watcher.leadership),
            leadership_guard=forbidden,
        )
    factory.close()
    with pytest.raises(TypeError, match="open Controller factory"):
        compose_postgres_worker({}, service, dependencies, leadership_guard=forbidden)


def test_missing_or_invalid_injection_cannot_fall_back(wiring, monkeypatch):
    _, dependencies, service = wiring
    monkeypatch.setattr(coder_runtime, "DispatchLedger", forbidden)
    monkeypatch.setattr(coder_runtime, "_SqliteCiTracking", forbidden)
    with pytest.raises(TypeError, match="leadership_guard"):
        compose_postgres_worker({}, service, dependencies)
    with pytest.raises(TypeError, match="Controller guard"):
        compose_postgres_worker({}, service, dependencies, leadership_guard=None)
    with pytest.raises(TypeError, match="must be injected"):
        coder_runtime.CoderRuntime({}, service)

    injected = {
        "dispatch": dependencies.dispatch,
        "ci": dependencies.ci_state.tracker,
        "leadership_guard": lambda: True,
    }
    for port in injected:
        for invalid in (None, object()):
            with pytest.raises(
                TypeError, match="dispatch evidence, CI tracking and leadership"
            ):
                coder_runtime.CoderRuntime({}, service, **(injected | {port: invalid}))


@pytest.mark.parametrize("guard_result", [None, False])
def test_callback_cannot_authorize_without_the_borrowed_lock(
    wiring, monkeypatch, guard_result
):
    _, dependencies, service = wiring
    # The real lock is unacquired. No runner or transport hooks may be entered.
    runtime = compose_postgres_worker(
        {}, service, dependencies, leadership_guard=lambda: guard_result
    )
    monkeypatch.setattr(coder_runtime, "_selected_host", forbidden)
    monkeypatch.setattr(coder_runtime, "_require_engine", forbidden)
    monkeypatch.setattr(runtime, "_code_request", forbidden)
    monkeypatch.setattr(runtime, "_prepare_stage", forbidden)
    monkeypatch.setattr(runtime, "_register_handoff", forbidden)
    monkeypatch.setattr(runtime.sandbox, "clear_engine", forbidden)
    monkeypatch.setattr(runtime, "stop", forbidden)
    with pytest.raises(PermissionError):
        runtime.execute(CLAIM, {})
    with pytest.raises(PermissionError):
        runtime.docker(CLAIM, None, "prepare")
    with pytest.raises(PermissionError):
        runtime.reconcile(DISPATCH)
    with pytest.raises(PermissionError):
        runtime.release_engine(DISPATCH)


@pytest.mark.parametrize("lose_after", ["engine_probe", "pre_launch_write"])
def test_loss_during_engine_admission_leaves_slot_active(
    wiring, monkeypatch, lose_after
):
    _, dependencies, service = wiring
    owns_lock = True
    calls = []

    def boundary(name, result=None):
        def observe(*args):
            nonlocal owns_lock
            calls.append(name)
            if name == lose_after:
                owns_lock = False
            return result

        return observe

    claim = CLAIM | {"deadline": coder_runtime.time.time() + 60}
    context = {
        "task_id": "T",
        "status": "AWAITING_HANDOFF",
        "completed_review_rounds": 0,
        "current_head_sha": None,
        "base_sha": INPUT,
    }
    monkeypatch.setattr(dependencies.leadership, "try_acquire", lambda: True)
    monkeypatch.setattr(dependencies.leadership, "is_held", lambda: owns_lock)
    monkeypatch.setattr(dependencies.leadership, "release", boundary("release"))
    monkeypatch.setattr(dependencies.dispatch, "claim", Mock(return_value=claim))
    monkeypatch.setattr(
        dependencies.dispatch, "retire_pre_launch", boundary("pre_launch_write")
    )
    monkeypatch.setattr(dependencies.dispatch, "block", forbidden)
    monkeypatch.setattr(dependencies.dispatch, "finish", forbidden)
    monkeypatch.setattr(service, "get_coder_assignment", lambda task_id: context)
    monkeypatch.setattr(coder_runtime, "_selected_host", lambda: "unix:///fixture.sock")
    monkeypatch.setattr(
        coder_runtime, "_require_engine", boundary("engine_probe", "daemon")
    )

    def execute(claim, context, guard):
        runtime = compose_postgres_worker(
            {"model": "gpt-6-luna"}, service, dependencies, leadership_guard=guard
        )
        monkeypatch.setattr(runtime.sandbox, "record_engine", forbidden)
        monkeypatch.setattr(runtime, "_execute_bound", forbidden)
        monkeypatch.setattr(runtime, "stop", forbidden)
        runtime.execute(claim, context)

    with pytest.raises(PermissionError, match="leadership lost"):
        run_controller_once(dependencies, service, {"T"}, execute)
    dependencies.dispatch.claim.assert_called_once_with(
        {"T"}, 1800, track_pre_launch=True
    )
    expected = ["engine_probe"]
    if lose_after == "pre_launch_write":
        expected.append("pre_launch_write")
    assert calls == [*expected, "release"]


@pytest.mark.parametrize("tracked_head", [HEAD, INPUT])
def test_injected_ci_tracks_only_authorized_head_as_blocked(
    wiring, monkeypatch, tracked_head
):
    factory, dependencies, service = wiring
    runtime = compose_postgres_worker(
        {}, service, dependencies, leadership_guard=lambda: None
    )
    session = Mock()
    session.begin.return_value = nullcontext()
    session.get.side_effect = [
        SimpleNamespace(
            repository=REPOSITORY, current_head_sha=HEAD, status="READY_IN_REVIEW"
        ),
        None,
    ]
    monkeypatch.setattr(factory, "get_scoped_session", lambda: nullcontext(session))

    if tracked_head == INPUT:
        with pytest.raises(WorkflowError, match="authorized exact task HEAD"):
            runtime.agent_runtime.ci.execute("T", 7, tracked_head)
        session.add.assert_not_called()
    else:
        runtime.agent_runtime.ci.execute("T", 7, tracked_head)
        binding = session.add.call_args.args[0]
        assert (
            binding.task_id,
            binding.pr_number,
            binding.head_sha,
            binding.verdict,
        ) == ("T", 7, HEAD, "BLOCKED")
