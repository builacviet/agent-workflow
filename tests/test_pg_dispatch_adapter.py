"""PostgreSQL Dev dispatch and role-neutral persistence integration tests."""

import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from test_agent_workflow import HEAD, NEXT, spec

from agent_workflow.app_config import AppConfig
from agent_workflow.budget import dispatch_budget
from agent_workflow.composition.postgres_controller import (
    PostgresControllerDependencies,
)
from agent_workflow.composition.postgres_leader_runner import run_controller_once
from agent_workflow.domain.service import WorkflowService
from agent_workflow.infrastructure.postgres_leadership import (
    LeadershipDomain,
    PostgresLeadershipLock,
)
from agent_workflow.storage.dispatch_repository import DispatchLedger
from agent_workflow.storage.postgres_connection import PostgresFactory
from agent_workflow.storage.postgres_dispatch import PostgresDispatchLedger
from agent_workflow.storage.postgres_workflow import PostgresWorkflowRepository
from agent_workflow.storage.repository import WorkflowError


@pytest.fixture
def setup():
    dsn = os.environ.get("PG_TEST_DSN")
    if not dsn:
        pytest.skip("disposable PG_TEST_DSN not supplied")
    if make_url(dsn).database not in {"workflow_test", "workflow_ci"}:
        pytest.fail("dispatch tests require workflow_test or workflow_ci")
    task_id = "PG02C-" + uuid.uuid4().hex
    with PostgresFactory.open(AppConfig(pg_runtime_dsn=dsn)) as factory:
        service = WorkflowService(
            repository=PostgresWorkflowRepository(factory),
            require_ci=False,
            clock=lambda: 1000.0,
        )
        service.provision_task(spec(task_id))
        ledger = PostgresDispatchLedger(
            factory, role="dev", phase="code", clock=lambda: 1000.0
        )
        try:
            yield ledger, service, factory, task_id
        finally:
            with factory.transaction() as conn:
                conn.execute(
                    text(
                        "DELETE FROM agent_execution_evidence WHERE dispatch_id IN (SELECT dispatch_id FROM agent_dispatches WHERE task_id=:task)"
                    ),
                    {"task": task_id},
                )
                conn.execute(
                    text("DELETE FROM agent_dispatches WHERE task_id=:task"),
                    {"task": task_id},
                )
                conn.execute(
                    text("DELETE FROM task_events WHERE task_id=:task"),
                    {"task": task_id},
                )
                conn.execute(
                    text("DELETE FROM tasks WHERE task_id=:task"), {"task": task_id}
                )


def test_concurrent_claim_block_restart_and_replay(setup):
    ledger, _, factory, task_id = setup
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: ledger.claim({task_id}, 60, track_pre_launch=True), range(2)
            )
        )
    claims = [result for result in results if result is not None]
    assert len(claims) == 1
    claim = claims[0]
    restarted = PostgresDispatchLedger(
        factory, role="dev", phase="code", clock=lambda: 1000.0
    )
    assert restarted.is_pre_launch(claim["dispatch_id"])
    restarted.retire_pre_launch(claim["dispatch_id"])
    assert not restarted.is_pre_launch(claim["dispatch_id"])
    restarted.block(claim["dispatch_id"])
    assert restarted.claim({task_id}, 60) is None
    with pytest.raises(WorkflowError):
        restarted.finish(claim["dispatch_id"], HEAD)
    restarted.acknowledge(claim["dispatch_id"])
    assert restarted.claim({task_id}, 60) is None
    with pytest.raises(WorkflowError):
        restarted.acknowledge(claim["dispatch_id"])


def test_ambiguous_claim_reserves_slot_across_new_controller_owner(setup):
    ledger, service, factory, task_id = setup
    claim = ledger.claim({task_id}, 60, track_pre_launch=True)
    assert claim is not None
    simulated_publish = Mock()

    def next_owner_attempt():
        successor = PostgresDispatchLedger(
            factory, role="dev", phase="code", clock=lambda: 1000.0
        )
        leadership = PostgresLeadershipLock(factory, LeadershipDomain.CONTROLLER)
        dependencies = PostgresControllerDependencies(
            service.repository, successor, Mock(), leadership
        )
        return run_controller_once(dependencies, service, {task_id}, simulated_publish)

    assert not next_owner_attempt()
    assert ledger.is_pre_launch(claim["dispatch_id"])
    ledger.block(claim["dispatch_id"])
    assert not next_owner_attempt()
    ledger.acknowledge(claim["dispatch_id"])
    assert not next_owner_attempt()
    simulated_publish.assert_not_called()


def test_evidence_identity_and_finish(setup):
    ledger, _, _, task_id = setup
    claim = ledger.claim({task_id}, 60)
    evidence = (
        claim["dispatch_id"],
        task_id,
        claim["input_sha"],
        "codex",
        "1.0",
        None,
        "UNVERIFIED",
        "d" * 64,
        HEAD,
    )
    with pytest.raises(WorkflowError):
        ledger.record_runtime_evidence(
            claim["dispatch_id"], task_id, NEXT, *evidence[3:]
        )
    with pytest.raises(WorkflowError):
        ledger.record_runtime_evidence(claim["dispatch_id"], "wrong", *evidence[2:])
    ledger.record_runtime_evidence(*evidence)
    with pytest.raises(IntegrityError):
        ledger.record_runtime_evidence(*evidence)
    with pytest.raises(WorkflowError, match="HEAD mismatch"):
        ledger.finish(claim["dispatch_id"], NEXT)
    assert ledger.claim({task_id}, 60) is None
    ledger.finish(claim["dispatch_id"], HEAD)
    assert ledger.claim({task_id}, 60) is None


def test_unsupported_role_and_invalid_timeout(setup):
    ledger, _, factory, task_id = setup
    for role, phase in [("reviewer", "code"), ("dev", "test"), ("", "")]:
        with pytest.raises(WorkflowError):
            PostgresDispatchLedger(factory, role=role, phase=phase)
    for timeout in (0, 3601, True):
        with pytest.raises(WorkflowError):
            ledger.claim({task_id}, timeout)
    assert ledger.claim({task_id}, 60) is not None


def test_lock_wait_is_bounded_and_retries(setup):
    ledger, _, factory, task_id = setup
    with factory.transaction() as holder:
        holder.execute(text("SELECT pg_advisory_xact_lock(5727811, 2)"))
        started = time.monotonic()
        with pytest.raises(DBAPIError):
            with dispatch_budget(time.time() + 0.6):
                ledger.claim({task_id}, 60)
        assert time.monotonic() - started < 3
    assert ledger.claim({task_id}, 60) is not None


def test_new_round_and_sha_are_new_identity(setup):
    ledger, _, factory, task_id = setup
    first = ledger.claim({task_id}, 60)
    ledger.record_runtime_evidence(
        first["dispatch_id"],
        task_id,
        first["input_sha"],
        "codex",
        "1",
        None,
        "UNVERIFIED",
        "d" * 64,
        HEAD,
    )
    ledger.finish(first["dispatch_id"], HEAD)
    with factory.transaction() as conn:
        conn.execute(
            text(
                "UPDATE tasks SET status='CHANGES_REQUESTED', completed_review_rounds=1, current_head_sha=:sha WHERE task_id=:task"
            ),
            {"sha": HEAD, "task": task_id},
        )
    second = ledger.claim({task_id}, 60)
    assert second["round_number"] == 1 and second["input_sha"] == HEAD
    assert second["dispatch_id"] != first["dispatch_id"]


def test_sqlite_postgres_claim_parity_and_restart(setup, tmp_path):
    ledger, _, _, task_id = setup
    sqlite_service = WorkflowService(
        tmp_path / "dispatch.sqlite", require_ci=False, clock=lambda: 1000.0
    )
    sqlite_service.provision_task(spec(task_id))
    sqlite = DispatchLedger(sqlite_service, clock=lambda: 1000.0)
    pg_claim = ledger.claim({task_id}, 60, track_pre_launch=True)
    sqlite_claim = sqlite.claim({task_id}, 60, track_pre_launch=True)
    for claim in (pg_claim, sqlite_claim):
        claim.pop("dispatch_id")
    assert pg_claim == sqlite_claim
    dsn = os.environ["PG_TEST_DSN"]
    with PostgresFactory.open(AppConfig(pg_runtime_dsn=dsn)) as restarted_factory:
        restarted = PostgresDispatchLedger(restarted_factory, role="dev", phase="code")
        assert restarted.claim({task_id}, 60) is None
        with restarted_factory.transaction() as connection:
            dispatch_id = connection.execute(
                text("SELECT dispatch_id FROM agent_dispatches WHERE task_id=:task"),
                {"task": task_id},
            ).scalar_one()
        restarted.block(dispatch_id)
    with PostgresFactory.open(AppConfig(pg_runtime_dsn=dsn)) as restarted_factory:
        restarted = PostgresDispatchLedger(restarted_factory, role="dev", phase="code")
        assert restarted.claim({task_id}, 60) is None
        restarted.acknowledge(dispatch_id)
        assert restarted.claim({task_id}, 60) is None


def test_wrong_dispatch_and_role_cannot_record_evidence(setup):
    ledger, _, factory, task_id = setup
    claim = ledger.claim({task_id}, 60)
    evidence = (
        task_id,
        claim["input_sha"],
        "codex",
        "1",
        None,
        "UNVERIFIED",
        "d" * 64,
        HEAD,
    )
    with pytest.raises(WorkflowError, match="no active matching dispatch"):
        ledger.record_runtime_evidence("f" * 32, *evidence)
    with factory.transaction() as conn:
        conn.execute(
            text("UPDATE agent_dispatches SET role='other' WHERE dispatch_id=:id"),
            {"id": claim["dispatch_id"]},
        )
    with pytest.raises(WorkflowError, match="no active matching dispatch"):
        ledger.record_runtime_evidence(claim["dispatch_id"], *evidence)
    with pytest.raises(WorkflowError, match="lease lost"):
        ledger.finish(claim["dispatch_id"], HEAD)
    with factory.transaction() as conn:
        assert (
            conn.execute(
                text("SELECT status FROM agent_dispatches WHERE dispatch_id=:id"),
                {"id": claim["dispatch_id"]},
            ).scalar_one()
            == "ACTIVE"
        )


def test_claim_failure_rolls_back_and_allows_retry(setup, monkeypatch):
    from agent_workflow.storage.postgres_dispatch import DevClaim

    ledger, _, factory, task_id = setup

    def fail_validation(*args, **kwargs):
        raise RuntimeError("injected failure after flush")

    with monkeypatch.context() as patch:
        patch.setattr(DevClaim, "model_validate", fail_validation)
        with pytest.raises(RuntimeError, match="injected failure"):
            ledger.claim({task_id}, 60)
    with factory.transaction() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM agent_dispatches WHERE task_id=:task"),
                {"task": task_id},
            ).scalar_one()
            == 0
        )
    assert ledger.claim({task_id}, 60) is not None


@pytest.mark.parametrize("failed_check", [1, 2])
def test_claim_deadline_before_outer_commit_releases_slot(
    setup, monkeypatch, failed_check
):
    ledger, _, factory, task_id = setup
    checks = 0

    def fail_at_deadline():
        nonlocal checks
        checks += 1
        if checks == failed_check:
            raise TimeoutError("deadline expired")

    with monkeypatch.context() as patch:
        patch.setattr(
            "agent_workflow.storage.postgres_dispatch.check_deadline",
            fail_at_deadline,
        )
        with pytest.raises(TimeoutError, match="deadline expired"):
            ledger.claim({task_id}, 60)
    assert checks == failed_check
    with factory.transaction() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM agent_dispatches WHERE task_id=:task_id"),
                {"task_id": task_id},
            )
            == 0
        )
    assert ledger.claim({task_id}, 60) is not None


def test_finish_without_evidence_fails_and_retains_slot(setup):
    ledger, _, factory, task_id = setup
    claim = ledger.claim({task_id}, 60)
    with pytest.raises(WorkflowError, match="runtime evidence required"):
        ledger.finish(claim["dispatch_id"], HEAD)
    with factory.transaction() as conn:
        row = conn.execute(
            text(
                "SELECT status,slot,result_sha FROM agent_dispatches WHERE dispatch_id=:id"
            ),
            {"id": claim["dispatch_id"]},
        ).one()
        assert tuple(row) == ("ACTIVE", 1, None)
    assert ledger.claim({task_id}, 60) is None
    ledger.record_runtime_evidence(
        claim["dispatch_id"],
        task_id,
        claim["input_sha"],
        "codex",
        "1",
        None,
        "UNVERIFIED",
        "d" * 64,
        HEAD,
    )
    ledger.finish(claim["dispatch_id"], HEAD)
    with factory.transaction() as conn:
        row = conn.execute(
            text(
                "SELECT status,slot,result_sha FROM agent_dispatches WHERE dispatch_id=:id"
            ),
            {"id": claim["dispatch_id"]},
        ).one()
        assert tuple(row) == ("DONE", None, HEAD)


def test_tampered_evidence_identity_keeps_slot(setup):
    ledger, _, factory, task_id = setup
    claim = ledger.claim({task_id}, 60)
    ledger.record_runtime_evidence(
        claim["dispatch_id"],
        task_id,
        claim["input_sha"],
        "codex",
        "1",
        None,
        "UNVERIFIED",
        "d" * 64,
        HEAD,
    )
    with factory.transaction() as conn:
        conn.execute(
            text(
                "UPDATE agent_execution_evidence SET input_sha=:sha WHERE dispatch_id=:id"
            ),
            {"sha": NEXT, "id": claim["dispatch_id"]},
        )
    with pytest.raises(WorkflowError, match="identity mismatch"):
        ledger.finish(claim["dispatch_id"], HEAD)
    assert ledger.claim({task_id}, 60) is None


@pytest.mark.parametrize("fault", [None, "finish", "cleanup", "ownership"])
def test_controller_entrypoint_fake_worker_pg_lifecycle(
    setup, monkeypatch, tmp_path, fault
):
    """Hosted PG16 module: real composition/state; synthetic external effects only."""
    from types import SimpleNamespace

    from test_agent_runtime import FakeHost

    from agent_workflow.composition import postgres_dispatch_entrypoint as entry
    from agent_workflow.runners import coder_runtime

    _, _, observer, task_id = setup
    monkeypatch.setenv("WFC_SANDBOX_STATE_DIR", str(tmp_path / "fences"))
    app = AppConfig(pg_runtime_dsn=os.environ["PG_TEST_DSN"])
    # Admission settings are covered offline with inert credential fixtures.
    # This PG test targets the real factory/guard/adapters and fake Worker seam.
    monkeypatch.setattr(entry, "_validate", lambda *_: None)
    opened = []
    original_open = PostgresFactory.open
    original_compose = entry.compose_postgres_worker
    original_controller = entry.compose_postgres_controller
    calls = []
    pins = set()
    dispatch_ids = []

    def open_factory(settings):
        factory = original_open(settings)
        opened.append(factory)
        return factory

    def compose_controller(factory):
        composition = original_controller(factory)
        if fault == "finish":
            monkeypatch.setattr(
                composition.controller.dispatch,
                "finish",
                Mock(side_effect=RuntimeError("uncertain finish")),
            )
        return composition

    def compose_worker(config, service, deps, *, leadership_guard):
        runtime = original_compose(
            config, service, deps, leadership_guard=leadership_guard
        )
        assert service.repository is deps.workflow
        assert runtime.agent_runtime.dispatch is deps.dispatch
        assert runtime.agent_runtime.ci is deps.ci_state
        host = FakeHost(calls)
        host._validated_result = lambda *_: {
            "head_sha": HEAD,
            "pr_url": "https://github.com/builacviet/agent-workflow/pull/7",
        }

        def handoff(context, result, summary):
            leadership_guard()
            calls.append("handoff")
            service.register_task_handoff(
                task_id,
                context["repository"],
                context["branch"],
                context["base_sha"],
                result["head_sha"],
                "docs/handoff.md",
                summary,
            )

        host._register_handoff = handoff
        runtime.agent_runtime.host = host

        def execute(claim, context):
            leadership_guard()
            dispatch_ids.append(claim["dispatch_id"])
            assert claim["task_id"] == task_id
            assert deps.dispatch.is_pre_launch(claim["dispatch_id"])
            deps.dispatch.retire_pre_launch(claim["dispatch_id"])
            assert not deps.dispatch.is_pre_launch(claim["dispatch_id"])
            pins.add(claim["dispatch_id"])
            result = runtime.agent_runtime.execute_bound(
                claim,
                context,
                SimpleNamespace(spec=SimpleNamespace(runtime="fake")),
                SimpleNamespace(),
                None,
            )
            if fault == "ownership":
                # Simulate abrupt session loss after evidence/handoff/CI commit.
                deps.leadership.release()
            return result

        def clear(dispatch_id):
            leadership_guard()
            with observer.transaction() as conn:
                assert (
                    conn.execute(
                        text(
                            "SELECT status FROM agent_dispatches WHERE dispatch_id=:id"
                        ),
                        {"id": dispatch_id},
                    ).scalar_one()
                    == "DONE"
                )
            calls.append("clear")
            if fault == "cleanup":
                raise RuntimeError("uncertain cleanup")
            pins.remove(dispatch_id)

        monkeypatch.setattr(runtime, "execute", execute)
        monkeypatch.setattr(runtime, "release_engine", clear)
        return runtime

    monkeypatch.setattr(entry.PostgresFactory, "open", open_factory)
    monkeypatch.setattr(entry, "compose_postgres_controller", compose_controller)
    monkeypatch.setattr(entry, "compose_postgres_worker", compose_worker)
    monkeypatch.setattr(
        coder_runtime, "docker_command", lambda *_: pytest.fail("real Docker forbidden")
    )
    monkeypatch.setattr(
        coder_runtime, "run_container", lambda *_: pytest.fail("real Worker forbidden")
    )
    config = {"tasks": {task_id: {}}}
    try:
        if fault:
            with pytest.raises((RuntimeError, WorkflowError, PermissionError)):
                entry.run_postgres_dispatch_once(config, app)
        else:
            assert entry.run_postgres_dispatch_once(config, app)
        assert len(opened) == 1 and opened[0]._closed
        assert len(dispatch_ids) == 1
        with observer.transaction() as conn:
            row = conn.execute(
                text(
                    "SELECT status,slot,result_sha FROM agent_dispatches WHERE dispatch_id=:id"
                ),
                {"id": dispatch_ids[0]},
            ).one()
            expected = (
                "BLOCKED"
                if fault == "finish"
                else "ACTIVE"
                if fault == "ownership"
                else "DONE"
            )
            assert tuple(row) == (
                expected,
                1 if expected != "DONE" else None,
                HEAD if expected == "DONE" else None,
            )
            binding = conn.execute(
                text(
                    "SELECT head_sha,verdict FROM controller_ci_tasks WHERE task_id=:task"
                ),
                {"task": task_id},
            ).one()
            assert tuple(binding) == (HEAD, "BLOCKED")
            assert (
                conn.execute(
                    text(
                        "SELECT published_head_sha FROM agent_execution_evidence WHERE dispatch_id=:id"
                    ),
                    {"id": dispatch_ids[0]},
                ).scalar_one()
                == HEAD
            )
        assert bool(pins) == bool(fault)
        assert ("clear" in calls) == (fault in {None, "cleanup"})
        # A fresh owner and factory must never replay the synthetic publication,
        # even when the first owner crashed after the handoff or cleanup failed.
        assert not entry.run_postgres_dispatch_once(config, app)
        assert len(dispatch_ids) == 1
        assert calls.count("publish") == 1
        assert all(factory._closed for factory in opened)
    finally:
        with observer.transaction() as conn:
            conn.execute(
                text("DELETE FROM controller_ci_tasks WHERE task_id=:task"),
                {"task": task_id},
            )
