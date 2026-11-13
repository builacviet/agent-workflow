"""Live PostgreSQL Controller CI evidence and signed-inbox settlement."""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, event, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool
from test_ci_controller import FakeChecks
from test_my_wfc import BASE, HEAD, NEXT, REPO, spec

from agent_workflow.app_config import AppConfig
from agent_workflow.domain.service import WorkflowService
from agent_workflow.integrations.ci_controller import ControllerCiCoordinator
from agent_workflow.integrations.ci_gate import CiEvidence
from agent_workflow.integrations.github import WorkflowError
from agent_workflow.storage.postgres_ci_state import PostgresControllerCiState
from agent_workflow.storage.postgres_connection import (
    PostgresFactory,
    PostgresUnavailableError,
)
from agent_workflow.storage.postgres_models import (
    CIObservation,
    CIProcessedDelivery,
    CITask,
    Task,
    TaskEvent,
    WebhookInboxRow,
)
from agent_workflow.storage.postgres_webhook import PostgresWebhookInbox
from agent_workflow.storage.postgres_workflow import PostgresWorkflowRepository
from agent_workflow.storage.webhook_inbox import InboxResult, WebhookNotification

PR = 21
INSTALLATION = 17
COMPLETED = "2026-11-10T00:00:00Z"
OBSERVED = "2026-11-10T00:01:00Z"


@pytest.fixture
def setup():
    dsn = os.environ.get("PG_TEST_DSN")
    if not dsn:
        pytest.skip("disposable PG_TEST_DSN not supplied")
    if make_url(dsn).database not in {"workflow_test", "workflow_ci"}:
        pytest.fail("CI state tests require workflow_test or workflow_ci")
    config = AppConfig(pg_runtime_dsn=dsn)
    task_id = f"PG02E-{uuid4().hex}"
    first, second = sorted(str(uuid4()) for _ in range(2))
    deliveries = [
        WebhookNotification(
            delivery, "workflow_run", "completed", REPO, INSTALLATION, 44, NEXT
        )
        for delivery in (first, second)
    ]
    with PostgresFactory.open(config) as factory:
        service = WorkflowService(repository=PostgresWorkflowRepository(factory))
        service.provision_task(spec(task_id))
        try:
            yield config, factory, service, task_id, deliveries
        finally:
            with factory.transaction() as conn:
                for row in (CIObservation, CITask, TaskEvent, Task):
                    conn.execute(delete(row).where(row.task_id == task_id))
                for row in (CIProcessedDelivery, WebhookInboxRow):
                    conn.execute(
                        delete(row).where(row.delivery_id.in_([first, second]))
                    )


def handoff(service, task_id, head):
    service.register_task_handoff(
        task_id, REPO, "task/test", BASE, head, "docs/handoff.md", "test"
    )


def evidence(task_id, verdict="SUCCESS", head=HEAD):
    return CiEvidence(
        task_id,
        REPO,
        INSTALLATION,
        PR,
        head,
        head,
        head,
        "Python tests",
        "github-actions",
        verdict,
        {
            "SUCCESS": "REQUIRED_CHECK_PASSED",
            "FAILURE": "REQUIRED_CHECK_FAILED",
            "BLOCKED": "CHECK_PENDING",
        }[verdict],
        7,
        44,
        1,
        COMPLETED,
        OBSERVED,
    )


def observations(factory, task_id):
    with factory.transaction() as conn:
        return (
            conn.execute(
                select(CIObservation.__table__)
                .where(CIObservation.task_id == task_id)
                .order_by(CIObservation.id)
            )
            .mappings()
            .all()
        )


def add_delivery(factory, notification):
    digest = sha256(notification.delivery_id.encode()).hexdigest()
    inbox = PostgresWebhookInbox(factory)
    assert inbox.record(notification, digest) is InboxResult.INSERTED
    assert inbox.record(notification, digest) is InboxResult.DUPLICATE


def run_race(*operations):
    barrier = Barrier(len(operations), timeout=5)

    def start(operation):
        barrier.wait()
        operation()

    with ThreadPoolExecutor(max_workers=len(operations)) as executor:
        futures = [executor.submit(start, operation) for operation in operations]
        for future in futures:
            future.result(timeout=15)


def test_authorized_binding_and_exact_identity(setup):
    _, factory, service, task_id, _ = setup
    state = PostgresControllerCiState(factory)
    with pytest.raises(WorkflowError, match="authorized exact task HEAD"):
        state.track(task_id, PR, HEAD)
    handoff(service, task_id, HEAD)
    for invalid_task, invalid_pr, invalid_head in [
        ("unknown", PR, HEAD),
        (task_id, True, HEAD),
        (task_id, PR, NEXT),
        (task_id, PR, HEAD.upper()),
    ]:
        with pytest.raises(WorkflowError):
            state.track(invalid_task, invalid_pr, invalid_head)
    with factory.transaction() as conn:
        conn.execute(
            update(Task).where(Task.task_id == task_id).values(repository="other/repo")
        )
    with pytest.raises(WorkflowError, match="authorized exact task HEAD"):
        state.track(task_id, PR, HEAD)
    with factory.transaction() as conn:
        conn.execute(
            update(Task).where(Task.task_id == task_id).values(repository=REPO)
        )

    run_race(
        lambda: state.track(task_id, PR, HEAD), lambda: state.track(task_id, PR, HEAD)
    )
    with pytest.raises(WorkflowError, match="PR differs"):
        state.track(task_id, PR + 1, HEAD)
    assert state.tracked() == [
        {
            "task_id": task_id,
            "pr_number": PR,
            "head_sha": HEAD,
            "verdict": "BLOCKED",
            "repair_heads": 0,
        }
    ]
    assert not state.approved(task_id, HEAD)
    assert not state.approved(task_id, "abbreviated")
    assert not state.approved(task_id, NEXT)

    valid = evidence(task_id)
    with pytest.raises(WorkflowError, match="untrusted CI evidence"):
        state.record(valid.as_dict())
    for overrides in (
        {"repository": "other/repo"},
        {"pr_number": PR + 1},
        {"task_id": "unknown"},
        {"check_app_slug": "untrusted"},
        {"check_name": "other check"},
        {"installation_id": True},
        {"run_attempt": None},
        {"completed_at": None},
        {"observed_head_sha": NEXT},
        {"tested_sha": NEXT},
    ):
        with pytest.raises(WorkflowError):
            state.record(replace(valid, **overrides))
    assert observations(factory, task_id) == []
    state.record(valid)
    assert state.approved(task_id, HEAD)
    with factory.transaction() as conn:
        conn.execute(
            update(CIObservation)
            .where(CIObservation.task_id == task_id)
            .values(pr_number=PR + 1)
        )
    assert not state.approved(task_id, HEAD)
    with factory.transaction() as conn:
        conn.execute(
            update(CIObservation)
            .where(CIObservation.task_id == task_id)
            .values(pr_number=PR)
        )

    # A changed task HEAD invalidates the old binding even before CI reconciliation.
    with factory.transaction() as conn:
        conn.execute(
            update(Task).where(Task.task_id == task_id).values(current_head_sha=NEXT)
        )
    assert not state.approved(task_id, HEAD)
    assert state.tracked() == []
    with pytest.raises(WorkflowError, match="HEAD differs"):
        state.track(task_id, PR, NEXT)
    with pytest.raises(WorkflowError, match="mismatched"):
        state.record(valid)
    assert len(observations(factory, task_id)) == 1


def test_concurrent_observations_keep_sticky_failure_and_history(setup):
    config, factory, service, task_id, _ = setup
    handoff(service, task_id, HEAD)
    state = PostgresControllerCiState(factory)
    state.track(task_id, PR, HEAD)
    failure = evidence(task_id, "FAILURE")
    success = replace(
        evidence(task_id), check_run_id=8, workflow_run_id=45, run_attempt=2
    )
    run_race(lambda: state.record(failure), lambda: state.record(success))

    with PostgresFactory.open(config) as restarted:
        state = PostgresControllerCiState(restarted)
        state.record(success)
        state.record(evidence(task_id, "BLOCKED"))
        assert state.previous_failure(task_id, HEAD)
        assert not state.previous_failure(task_id, NEXT)
        assert not state.approved(task_id, HEAD)
        assert state.tracked()[0]["verdict"] == "FAILURE"
        rows = observations(restarted, task_id)
        assert sorted(
            (
                row["verdict"],
                row["check_run_id"],
                row["workflow_run_id"],
                row["run_attempt"],
            )
            for row in rows
        ) == [
            ("BLOCKED", 7, 44, 1),
            ("FAILURE", 7, 44, 1),
            ("SUCCESS", 8, 45, 2),
            ("SUCCESS", 8, 45, 2),
        ]
        assert {
            (row["task_id"], row["pr_number"], row["head_sha"], row["observed_at"])
            for row in rows
        } == {(task_id, PR, HEAD, OBSERVED)}


def test_repair_limit_uses_existing_workflow_authorization(setup):
    _, factory, service, task_id, _ = setup
    state = PostgresControllerCiState(factory)
    handoff(service, task_id, HEAD)
    state.track(task_id, PR, HEAD)
    state.record(evidence(task_id, "BLOCKED"))
    with pytest.raises(WorkflowError, match="repair"):
        handoff(service, task_id, NEXT)
    state.record(evidence(task_id, "FAILURE"))

    previous = HEAD
    for count, head in enumerate((NEXT, "c" * 40), 1):
        handoff(service, task_id, head)
        state.track(task_id, PR, head)
        assert state.tracked()[0] == {
            "task_id": task_id,
            "pr_number": PR,
            "head_sha": head,
            "verdict": "BLOCKED",
            "repair_heads": count,
        }
        with pytest.raises(WorkflowError, match="mismatched"):
            state.record(evidence(task_id, head=previous))
        state.record(evidence(task_id, "FAILURE", head))
        previous = head

    with pytest.raises(WorkflowError, match="stale"):
        handoff(service, task_id, HEAD)
    with pytest.raises(WorkflowError, match="repair"):
        handoff(service, task_id, "d" * 40)
    assert state.tracked()[0]["repair_heads"] == 2
    assert [row["head_sha"] for row in observations(factory, task_id)] == [
        HEAD,
        HEAD,
        NEXT,
        previous,
    ]


def test_reconcile_blocks_incomplete_or_synthetic_evidence_and_recovers(setup):
    config, factory, service, task_id, deliveries = setup
    handoff(service, task_id, HEAD)
    state = PostgresControllerCiState(factory)
    state.track(task_id, PR, HEAD)
    delivery = deliveries[0]
    add_delivery(factory, delivery)

    class Checks(FakeChecks):
        phase = "missing"

        def list_check_runs(self, repo, sha):
            result = super().list_check_runs(repo, sha)
            if self.phase == "missing":
                return {"total_count": 0, "check_runs": []}
            if self.phase == "incomplete":
                result["total_count"] += 1
            if self.phase == "merge":
                result["check_runs"][0].update(event="pull_request", tested_sha=NEXT)
            return result

    checks = Checks(head=HEAD)
    coordinator = ControllerCiCoordinator(state, checks, installation_id=INSTALLATION)
    for phase, reason in [
        ("missing", "REQUIRED_CHECK_MISSING"),
        ("incomplete", "CHECK_HISTORY_INCOMPLETE"),
        ("merge", "REQUIRED_CHECK_MISSING"),
    ]:
        checks.phase = phase
        (result,) = coordinator.reconcile_once()
        assert (result.verdict, result.reason) == ("BLOCKED", reason)
        assert state.pending_events() == [delivery]
        assert not state.approved(task_id, HEAD)

    with PostgresFactory.open(config) as restarted:
        state = PostgresControllerCiState(restarted)
        coordinator = ControllerCiCoordinator(
            state, FakeChecks(head=HEAD), installation_id=INSTALLATION
        )
        (result,) = coordinator.reconcile_once()
        assert result.verdict == "SUCCESS"
        assert state.approved(task_id, HEAD)
        assert state.pending_events() == []
        rows = observations(restarted, task_id)
        assert len(rows) == 4
        assert [row["verdict"] for row in rows] == [
            "BLOCKED",
            "BLOCKED",
            "BLOCKED",
            "SUCCESS",
        ]
        assert rows[-1]["reason"] == result.reason
        assert rows[-1]["observed_at"] == result.observed_at
        assert (
            rows[-1]["check_run_id"],
            rows[-1]["workflow_run_id"],
            rows[-1]["run_attempt"],
        ) == (3, 44, 1)


def test_pending_delivery_order_concurrent_settlement_and_fk(setup):
    config, factory, _, task_id, (first, second) = setup
    state = PostgresControllerCiState(factory)
    add_delivery(factory, second)
    add_delivery(factory, first)
    assert state.pending_events(limit=1) == [first]
    assert state.pending_events() == [first, second]
    for invalid_limit in (0, 21, True):
        with pytest.raises(ValueError, match="bounded"):
            state.pending_events(invalid_limit)
    with pytest.raises(WorkflowError, match="invalid"):
        state.mark_processed(first.delivery_id.upper())
    missing = str(uuid4())
    with pytest.raises(WorkflowError, match="unknown"):
        state.mark_processed(missing)
    with pytest.raises(IntegrityError) as fk:
        with factory.transaction() as conn:
            conn.execute(
                CIProcessedDelivery.__table__.insert().values(delivery_id=missing)
            )
    assert fk.value.orig.sqlstate == "23503"

    run_race(
        lambda: state.mark_processed(first.delivery_id),
        lambda: state.mark_processed(first.delivery_id),
    )
    with PostgresFactory.open(config) as restarted:
        state = PostgresControllerCiState(restarted)
        assert state.pending_events() == [second]
        state.mark_processed(first.delivery_id)
        with restarted.transaction() as conn:
            assert conn.execute(
                select(CIProcessedDelivery.delivery_id).where(
                    CIProcessedDelivery.delivery_id.in_(
                        [first.delivery_id, second.delivery_id]
                    )
                )
            ).scalars().all() == [first.delivery_id]
        assert not state.approved(task_id, HEAD)


def test_verdict_write_failure_rolls_back_already_inserted_observation(setup):
    config, factory, service, task_id, _ = setup
    handoff(service, task_id, HEAD)
    state = PostgresControllerCiState(factory)
    state.track(task_id, PR, HEAD)
    state.record(evidence(task_id, "BLOCKED"))
    engine = create_engine(
        config.pg_runtime_dsn, poolclass=NullPool, hide_parameters=True
    )
    attempted = []

    @event.listens_for(engine, "before_cursor_execute")
    def fail_verdict_update(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE controller_ci_tasks SET verdict"):
            cursor.execute(
                "SELECT count(*) FROM controller_ci_observations WHERE task_id = %s",
                (task_id,),
            )
            attempted.append(cursor.fetchone()[0])
            cursor.execute("SELECT 1 / 0")

    with PostgresFactory(engine) as failing:
        with pytest.raises(
            PostgresUnavailableError,
            match="^PostgreSQL Controller CI operation failed$",
        ) as error:
            PostgresControllerCiState(failing).record(evidence(task_id))
        assert error.value.__cause__ is None

    assert attempted == [2]
    assert [row["verdict"] for row in observations(factory, task_id)] == ["BLOCKED"]
    assert state.tracked()[0]["verdict"] == "BLOCKED"
    assert not state.approved(task_id, HEAD)
    state.record(evidence(task_id))
    assert state.approved(task_id, HEAD)
