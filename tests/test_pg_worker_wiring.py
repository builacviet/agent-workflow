"""Offline Worker composition and admission tests for the PostgreSQL runtime."""

import sqlite3
from contextlib import nullcontext
from dataclasses import asdict, replace
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from sqlalchemy import create_engine
from test_runtime_plugins import FakePlugin

from agent_workflow.app_config import AppConfig
from agent_workflow.application.ci.use_cases.track_ci_task import TrackCiTaskUseCase
from agent_workflow.application.dispatch.ports import DispatchClaim
from agent_workflow.application.dispatch.run_dispatch_pass import RunDispatchPassUseCase
from agent_workflow.composition import ci_deps, worker_deps
from agent_workflow.composition.ci_deps import PostgresCiWriteScope
from agent_workflow.composition.postgres_controller import compose_postgres_controller
from agent_workflow.composition.worker_deps import compose_postgres_worker
from agent_workflow.domain.exceptions import BusinessRuleError
from agent_workflow.domain.workflow_identity import REPOSITORY
from agent_workflow.infrastructure.database.repositories.ci_state_repository import (
    SqlAlchemyCiStateRepository,
)
from agent_workflow.infrastructure.database.session_factory import PostgresFactory
from agent_workflow.infrastructure.postgres_leadership import (
    LeadershipDomain,
    PostgresLeadershipLock,
)
from agent_workflow.infrastructure.runtime import sandbox
from agent_workflow.infrastructure.runtime.codex_plugin import CodexCLIPlugin
from agent_workflow.infrastructure.runtime.registry import RuntimeRegistry
from agent_workflow.runners import coder_runtime

DISPATCH = "a" * 32
INPUT = "b" * 40
HEAD = "c" * 40
CLAIM = {"dispatch_id": DISPATCH, "task_id": "T", "input_sha": INPUT, "round_number": 0}


@pytest.fixture
def wiring(monkeypatch, tmp_path):
    monkeypatch.delenv("PG_RUNTIME_DSN", raising=False)
    app_config = AppConfig(sandbox_state_dir=tmp_path / "fences")
    monkeypatch.setenv("WFC_SANDBOX_STATE_DIR", str(tmp_path / "ambient"))
    monkeypatch.setattr(PostgresFactory, "assert_schema_ready", lambda self: None)
    factory = PostgresFactory(create_engine("postgresql+psycopg://", creator=forbidden))
    dependencies = compose_postgres_controller(factory)
    service = dependencies.handoff_provider
    return factory, dependencies, service, app_config


def forbidden(*args, **kwargs):
    pytest.fail("unexpected external operation or legacy SQLite path")


@pytest.fixture
def forbid_worker_effects(monkeypatch):
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(worker_deps, "SandboxManager", forbidden)
    monkeypatch.setattr(AppConfig, "from_env", forbidden)
    monkeypatch.setattr(sandbox, "docker_command", forbidden)
    monkeypatch.setattr(sandbox, "run_container", forbidden)


def _codex_registry():
    return RuntimeRegistry((CodexCLIPlugin(),))


def test_borrows_exact_dependencies_without_sqlite_or_external_work(
    wiring, monkeypatch
):
    factory, dependencies, service, app_config = wiring
    monkeypatch.setattr(factory, "get_scoped_session", forbidden)
    monkeypatch.setattr(coder_runtime, "_require_engine", forbidden)
    monkeypatch.setattr(PostgresFactory, "__init__", forbidden)
    monkeypatch.setattr(factory, "connect_session", forbidden)
    connect = Mock(side_effect=forbidden)
    dispose = Mock(side_effect=forbidden)
    monkeypatch.setattr(factory._engine, "connect", connect)
    monkeypatch.setattr(factory._engine, "dispose", dispose)
    monkeypatch.setattr(dependencies.leadership, "is_held", forbidden)

    registry = RuntimeRegistry((FakePlugin(),))
    manager = sandbox.SandboxManager(app_config.sandbox_state_dir)
    build_sandbox = Mock(return_value=manager)
    monkeypatch.setattr(worker_deps, "SandboxManager", build_sandbox)
    monkeypatch.setattr(AppConfig, "from_env", forbidden)
    runtime = compose_postgres_worker(
        {"database": "legacy.db"},
        service,
        dependencies,
        leadership_guard=forbidden,
        registry=registry,
        app_config=app_config,
    )
    assert runtime.registry is registry
    assert runtime.sandbox is manager
    build_sandbox.assert_called_once_with(fence_dir=app_config.sandbox_state_dir)
    assert not app_config.sandbox_state_dir.exists()

    assert runtime.handoff_provider is dependencies.handoff_provider
    assert runtime.agent_runtime.dispatch is dependencies.dispatch
    assert runtime.agent_runtime.ci is dependencies.ci_tracking
    assert runtime.agent_runtime.require_ci_tracking is True
    assert dependencies.ci_write_scope.factory is factory
    connect.assert_not_called()
    dispose.assert_not_called()


def test_accepts_equivalent_deferred_factory_callable(wiring, monkeypatch):
    factory, _, _, app_config = wiring
    session = Mock()
    session_scope = nullcontext(session)
    open_scope = Mock(return_value=session_scope)
    deferred = partial(open_scope, factory)
    monkeypatch.setattr(factory, "get_scoped_session", deferred)
    dependencies = compose_postgres_controller(factory)
    service = dependencies.handoff_provider

    runtime = compose_postgres_worker(
        {},
        service,
        dependencies,
        leadership_guard=forbidden,
        registry=_codex_registry(),
        app_config=app_config,
    )

    assert runtime.agent_runtime.ci is dependencies.ci_tracking
    open_scope.assert_not_called()
    with dependencies.ci_write_scope() as (uow, repository):
        assert uow._session is repository._session is session
    open_scope.assert_called_once_with(factory)


@pytest.mark.parametrize(
    "missing",
    [
        "handoff_provider",
        "dispatch",
        "ci_tracking",
        "ci_write_scope",
        "ci_factory",
        "leadership",
    ],
)
def test_missing_or_wrong_controller_dependency_fails_closed(
    wiring, forbid_worker_effects, monkeypatch, missing
):
    _, dependencies, service, app_config = wiring
    monkeypatch.setattr(worker_deps, "CoderRuntime", forbidden)
    for invalid in (None, object()):
        with pytest.raises(TypeError, match="Worker requires"):
            compose_postgres_worker(
                {},
                service,
                replace(dependencies, **{missing: invalid}),
                leadership_guard=forbidden,
                registry=_codex_registry(),
                app_config=app_config,
            )


@pytest.mark.parametrize(
    "foreign",
    [
        ("dispatch",),
        ("ci_factory",),
        ("leadership",),
        ("ci_tracking", "ci_write_scope"),
    ],
)
def test_mixed_factories_are_rejected(
    wiring, forbid_worker_effects, monkeypatch, foreign
):
    factory, dependencies, service, app_config = wiring
    other_factory = PostgresFactory(
        create_engine("postgresql+psycopg://", creator=forbidden)
    )
    monkeypatch.setattr(factory, "get_scoped_session", forbidden)
    monkeypatch.setattr(other_factory, "get_scoped_session", forbidden)
    other = compose_postgres_controller(other_factory)
    monkeypatch.setattr(worker_deps, "CoderRuntime", forbidden)
    with pytest.raises(TypeError, match="share the open Controller factory"):
        compose_postgres_worker(
            {},
            service,
            replace(dependencies, **{name: getattr(other, name) for name in foreign}),
            leadership_guard=forbidden,
            registry=_codex_registry(),
            app_config=app_config,
        )


@pytest.mark.parametrize(
    "binding", ["foreign_factory", "unbound_callable", "session_bound"]
)
def test_actual_ci_provider_and_session_binding_are_checked(
    wiring, forbid_worker_effects, monkeypatch, binding
):
    factory, dependencies, service, app_config = wiring
    monkeypatch.setattr(factory, "get_scoped_session", forbidden)
    if binding == "foreign_factory":
        other_factory = PostgresFactory(
            create_engine("postgresql+psycopg://", creator=forbidden)
        )
        monkeypatch.setattr(other_factory, "get_scoped_session", forbidden)
        scope = PostgresCiWriteScope(other_factory)
        expected = "share the open Controller factory"
    elif binding == "unbound_callable":
        scope = forbidden
        expected = "Worker requires"
    else:
        scope = SqlAlchemyCiStateRepository(session=Mock())
        expected = "Worker requires"
    substituted = replace(
        dependencies, ci_write_scope=scope, ci_tracking=TrackCiTaskUseCase(scope)
    )
    monkeypatch.setattr(worker_deps, "CoderRuntime", forbidden)

    with pytest.raises(TypeError, match=expected):
        compose_postgres_worker(
            {},
            service,
            substituted,
            leadership_guard=forbidden,
            registry=_codex_registry(),
            app_config=app_config,
        )


@pytest.mark.parametrize("foreign", ["ci_tracking", "ci_write_scope"])
def test_mixed_ci_operations_are_rejected(wiring, foreign):
    _, dependencies, service, app_config = wiring
    other = compose_postgres_controller(
        PostgresFactory(create_engine("postgresql+psycopg://", creator=forbidden))
    )
    with pytest.raises(TypeError, match="Worker requires"):
        compose_postgres_worker(
            {},
            service,
            replace(dependencies, **{foreign: getattr(other, foreign)}),
            leadership_guard=forbidden,
            registry=_codex_registry(),
            app_config=app_config,
        )


def test_mismatched_service_wrong_lock_or_closed_factory_rejected(wiring):
    factory, dependencies, service, app_config = wiring
    other = compose_postgres_controller(factory)
    unrelated_service = other.handoff_provider
    with pytest.raises(TypeError, match="composed PostgreSQL handoff provider"):
        compose_postgres_worker(
            {},
            unrelated_service,
            dependencies,
            leadership_guard=forbidden,
            registry=_codex_registry(),
            app_config=app_config,
        )
    with pytest.raises(TypeError, match="Controller guard"):
        compose_postgres_worker(
            {},
            service,
            replace(
                dependencies,
                leadership=PostgresLeadershipLock(factory, LeadershipDomain.CI_WATCHER),
            ),
            leadership_guard=forbidden,
            registry=_codex_registry(),
            app_config=app_config,
        )
    factory.close()
    with pytest.raises(TypeError, match="open Controller factory"):
        compose_postgres_worker(
            {},
            service,
            dependencies,
            leadership_guard=forbidden,
            registry=_codex_registry(),
            app_config=app_config,
        )


def test_released_lock_cannot_construct_worker(
    wiring, forbid_worker_effects, monkeypatch
):
    _, dependencies, service, app_config = wiring
    dependencies.leadership.release()
    monkeypatch.setattr(worker_deps, "CoderRuntime", forbidden)
    with pytest.raises(PermissionError, match="active Controller leadership"):
        compose_postgres_worker(
            {},
            service,
            dependencies,
            leadership_guard=forbidden,
            registry=_codex_registry(),
            app_config=app_config,
        )


def test_missing_or_invalid_controller_guard_cannot_construct_worker(
    wiring, forbid_worker_effects, monkeypatch
):
    _, dependencies, service, app_config = wiring
    monkeypatch.setattr(worker_deps, "CoderRuntime", forbidden)
    with pytest.raises(TypeError, match="leadership_guard"):
        compose_postgres_worker(
            {}, service, dependencies, registry=_codex_registry(), app_config=app_config
        )
    for invalid in (None, object()):
        with pytest.raises(TypeError, match="Controller guard"):
            compose_postgres_worker(
                {},
                service,
                dependencies,
                leadership_guard=invalid,
                registry=_codex_registry(),
                app_config=app_config,
            )


def test_missing_or_invalid_injection_cannot_fall_back(
    wiring, forbid_worker_effects, monkeypatch, tmp_path
):
    _, dependencies, service, app_config = wiring
    database = tmp_path / "misleading.sqlite"
    config = {"database": str(database)}
    monkeypatch.setattr(AppConfig, "from_env", forbidden)
    with pytest.raises(TypeError, match="required keyword-only arguments"):
        coder_runtime.CoderRuntime(config, service)
    injected = {
        "dispatch": dependencies.dispatch,
        "ci": dependencies.ci_tracking,
        "leadership_guard": lambda: True,
        "registry": _codex_registry(),
        "sandbox": sandbox.SandboxManager(app_config.sandbox_state_dir),
    }
    for port in injected:
        with pytest.raises(TypeError, match=port):
            coder_runtime.CoderRuntime(
                config,
                service,
                **{key: value for key, value in injected.items() if key != port},
            )
        for invalid in (None, object()):
            with pytest.raises(TypeError, match="Worker requires injected workflow"):
                coder_runtime.CoderRuntime(
                    config, service, **(injected | {port: invalid})
                )
    with pytest.raises(TypeError, match="Worker requires injected workflow"):
        coder_runtime.CoderRuntime(config, service, **dict.fromkeys(injected))
    for invalid in (
        sandbox.SandboxManager(),
        sandbox.SandboxManager(Path("relative")),
    ):
        with pytest.raises(TypeError, match="durable sandbox"):
            coder_runtime.CoderRuntime(
                config, service, **(injected | {"sandbox": invalid})
            )
    with pytest.raises(ValueError, match="safe, durable local directory"):
        coder_runtime.CoderRuntime(
            config,
            service,
            **(injected | {"sandbox": sandbox.SandboxManager(Path(tmp_path.anchor))}),
        )
    assert not database.exists()


def test_missing_or_invalid_app_config_cannot_construct_worker(
    wiring, forbid_worker_effects, monkeypatch
):
    _, dependencies, service, app_config = wiring
    monkeypatch.setattr(worker_deps, "CoderRuntime", forbidden)
    kwargs = {
        "leadership_guard": forbidden,
        "registry": _codex_registry(),
    }
    with pytest.raises(TypeError, match="app_config"):
        compose_postgres_worker({}, service, dependencies, **kwargs)
    for invalid in (None, object()):
        with pytest.raises(TypeError, match="explicit AppConfig"):
            compose_postgres_worker(
                {}, service, dependencies, app_config=invalid, **kwargs
            )
    invalid_path = app_config.model_copy(update={"sandbox_state_dir": "relative"})
    with pytest.raises(ValueError, match="safe, durable local directory"):
        compose_postgres_worker(
            {}, service, dependencies, app_config=invalid_path, **kwargs
        )


@pytest.mark.parametrize("guard_result", [None, False])
def test_callback_cannot_authorize_without_the_borrowed_lock(
    wiring, monkeypatch, guard_result
):
    _, dependencies, service, app_config = wiring
    # The real lock is unacquired. No runner or transport hooks may be entered.
    runtime = compose_postgres_worker(
        {},
        service,
        dependencies,
        leadership_guard=lambda: guard_result,
        registry=_codex_registry(),
        app_config=app_config,
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
    _, dependencies, service, app_config = wiring
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

    claim = DispatchClaim(**CLAIM, deadline=coder_runtime.time.time() + 60)
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
    assignment = Mock()
    assignment.get_coder_assignment.return_value = context
    monkeypatch.setattr(coder_runtime, "_selected_host", lambda: "unix:///fixture.sock")
    monkeypatch.setattr(
        coder_runtime, "_require_engine", boundary("engine_probe", "daemon")
    )

    def execute(claim, context, guard):
        runtime = compose_postgres_worker(
            {"model": "gpt-6-luna"},
            service,
            dependencies,
            leadership_guard=guard,
            registry=_codex_registry(),
            app_config=app_config,
        )
        monkeypatch.setattr(runtime.sandbox, "record_engine", forbidden)
        monkeypatch.setattr(runtime, "_execute_bound", forbidden)
        monkeypatch.setattr(runtime, "stop", forbidden)
        runtime.execute(asdict(claim), context)

    with pytest.raises(PermissionError, match="leadership lost"):
        RunDispatchPassUseCase(
            dependencies.leadership,
            dependencies.dispatch,
            assignment,
            execute,
        ).execute({"T"})
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
    factory, dependencies, service, app_config = wiring
    runtime = compose_postgres_worker(
        {},
        service,
        dependencies,
        leadership_guard=lambda: None,
        registry=_codex_registry(),
        app_config=app_config,
    )
    session = Mock()
    uow = MagicMock()
    make_uow = Mock(return_value=uow)
    monkeypatch.setattr(ci_deps, "SqlAlchemyUnitOfWork", make_uow)
    session.get.side_effect = [
        SimpleNamespace(
            repository=REPOSITORY, current_head_sha=HEAD, status="READY_IN_REVIEW"
        ),
        None,
    ]
    monkeypatch.setattr(factory, "get_scoped_session", lambda: nullcontext(session))

    if tracked_head == INPUT:
        with pytest.raises(BusinessRuleError, match="authorized exact task HEAD"):
            runtime.agent_runtime.ci.execute("T", 7, tracked_head)
        session.add.assert_not_called()
        uow.commit.assert_not_called()
    else:
        runtime.agent_runtime.ci.execute("T", 7, tracked_head)
        binding = session.add.call_args.args[0]
        assert (
            binding.task_id,
            binding.pr_number,
            binding.head_sha,
            binding.verdict,
        ) == ("T", 7, HEAD, "BLOCKED")
        uow.commit.assert_called_once_with()
    make_uow.assert_called_once_with(session=session, profile="ci_write")
