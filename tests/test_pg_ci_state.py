"""Live PostgreSQL Controller CI evidence and signed-inbox settlement."""

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
from threading import Barrier, Event
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
from uuid import uuid4

import pytest
from pg_task_fixtures import authorize_task, record_handoff
from sqlalchemy import create_engine, delete, event, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from test_agent_workflow import BASE, HEAD, NEXT, REPO, spec

from agent_workflow import budget
from agent_workflow.app_config import AppConfig
from agent_workflow.application.ci.dtos import CiTrackedTaskSnapshot
from agent_workflow.application.ci.use_cases.acknowledge_ci_event import (
    AcknowledgeCiEventUseCase,
)
from agent_workflow.application.ci.use_cases.check_ci_approval import (
    CheckCiApprovalUseCase,
)
from agent_workflow.application.ci.use_cases.record_ci_evidence import (
    RecordCiEvidenceUseCase,
)
from agent_workflow.application.ci.use_cases.track_ci_task import TrackCiTaskUseCase
from agent_workflow.application.exceptions import (
    ApplicationError,
    CorruptedPersistenceDataError,
    InvalidApplicationRequestError,
    PersistenceUnavailableError,
)
from agent_workflow.application.review.use_cases.write_contracts import ClaimReviewInput
from agent_workflow.bootstrap.mcp_runtime import postgres_review_writes_provider
from agent_workflow.composition.ci_deps import create_ci_state_adapters
from agent_workflow.composition.postgres_ci_entrypoint import compose_ci_execution
from agent_workflow.composition.task_handoff_deps import PostgresHandoffProvider
from agent_workflow.domain.ci_evidence import WebhookNotification
from agent_workflow.domain.ci_tracking import CiTrackingSnapshot
from agent_workflow.domain.task.handoff import HandoffRequest
from agent_workflow.domain.webhook.inbox import InboxResult
from agent_workflow.infrastructure.database.models import (
    CIObservation,
    CIProcessedDelivery,
    CITask,
    ReviewAttempt,
    Task,
    TaskEvent,
    WebhookInboxRow,
)
from agent_workflow.infrastructure.database.repositories.ci_state_reader import (
    SqlAlchemyCiStateReader,
)
from agent_workflow.infrastructure.database.repositories.ci_state_repository import (
    SqlAlchemyCiStateRepository,
)
from agent_workflow.infrastructure.database.repositories.task_handoff import (
    PostgresHandoffRepository,
)
from agent_workflow.infrastructure.database.repositories.webhook_inbox_repository import (
    SqlAlchemyWebhookInboxRepository,
)
from agent_workflow.infrastructure.database.session_factory import PostgresFactory
from agent_workflow.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from agent_workflow.infrastructure.postgres_leadership import (
    _WORKFLOW_NAMESPACE,
    LeadershipDomain,
    PostgresLeadershipLock,
)
from agent_workflow.integrations.ci_gate import CiEvidence
from agent_workflow.integrations.github import WorkflowError
from agent_workflow.interfaces.cli.ci_controller import (
    CiRunRequest,
    CiRunResponse,
    run_ci_once,
    run_ci_watch,
)

PR = 21
INSTALLATION = 17
CONTROLLER_CONFIG = {
    "controller_github_app": {
        "app_id": 1,
        "installation_id": INSTALLATION,
        "pem": "controller.pem",
    }
}
COMPLETED = "2026-11-10T00:00:00Z"
OBSERVED = "2026-11-10T00:01:00Z"


class FakeChecks:
    installation_id = 17

    def __init__(self, *, unavailable=False, head=HEAD):
        self.unavailable = unavailable
        self.head = head
        self.calls = 0

    def get_pull_request(self, repo, number):
        self.calls += 1
        if self.unavailable:
            raise OSError("offline")
        return {
            "number": number,
            "base": {"repo": {"full_name": repo}},
            "head": {"sha": self.head},
        }

    def list_check_runs(self, repo, sha):
        self.calls += 1
        return {
            "total_count": 1,
            "check_runs": [
                {
                    "id": 3,
                    "name": "Python tests",
                    "head_sha": sha,
                    "app": {"slug": "github-actions"},
                    "status": "completed",
                    "conclusion": "success",
                    "completed_at": "2026-11-10T00:00:00Z",
                    "event": "push",
                    "workflow_path": ".github/workflows/v1-ci.yml",
                    "tested_sha": sha,
                    "workflow_run_id": 44,
                    "run_attempt": 1,
                }
            ],
        }


def test_ci_dependencies_resolve_scoped_session_after_composition():
    factory = Mock(spec=PostgresFactory)
    original_scope = factory.get_scoped_session

    reader, repository = create_ci_state_adapters(factory)
    assert isinstance(reader, SqlAlchemyCiStateReader)
    assert isinstance(repository, SqlAlchemyCiStateRepository)
    assert reader._session_scope is repository._session_scope

    original_scope.assert_not_called()
    replacement_scope = Mock()
    factory.get_scoped_session = replacement_scope

    assert reader._session_scope() is replacement_scope.return_value
    assert repository._session_scope() is replacement_scope.return_value
    assert replacement_scope.call_count == 2


@pytest.mark.parametrize("fail_during_read", [False, True])
def test_ci_read_scope_rolls_back_without_commit(fail_during_read, monkeypatch):
    session = MagicMock()
    transaction = session.begin.return_value.__enter__.return_value
    transaction.is_active = True
    transaction.rollback.side_effect = lambda: setattr(transaction, "is_active", False)
    scope = MagicMock()
    scope.__enter__.return_value = session
    configure = Mock()
    monkeypatch.setattr(
        "agent_workflow.infrastructure.database.repositories.ci_state_reader."
        "_set_repository_transaction_options",
        configure,
    )
    reader = SqlAlchemyCiStateReader(lambda: scope)

    if fail_during_read:
        with pytest.raises(WorkflowError, match="forced read failure"):
            with reader._read_session() as active_session:
                assert active_session is session
                raise WorkflowError("forced read failure")
    else:
        with reader._read_session() as active_session:
            assert active_session is session

    configure.assert_called_once_with(session.connection.return_value, read_only=True)
    transaction.rollback.assert_called_once_with()
    session.commit.assert_not_called()
    scope.__exit__.assert_called_once()


@pytest.mark.parametrize(
    ("persisted_pr", "persisted_head", "expected_error"),
    [
        (PR, HEAD, None),
        (PR + 1, HEAD, "CI tracking PR differs"),
        (PR, NEXT, "CI tracking HEAD differs"),
    ],
)
def test_ci_tracking_use_case_load_decide_write_atomically(
    persisted_pr, persisted_head, expected_error
):
    """A fake scope proves the decision belongs in Application, not Infra."""
    task = CiTrackingSnapshot(
        "READY_IN_REVIEW", REPO, HEAD, persisted_pr, persisted_head
    )
    operation = Mock()
    operation.load_for_tracking.return_value = task
    scope = MagicMock()
    scope.__enter__.return_value = operation
    repository = Mock()
    repository.transaction.return_value = scope

    if expected_error:
        with pytest.raises(WorkflowError, match=expected_error):
            TrackCiTaskUseCase(repository).execute("sample", PR, HEAD)
        operation.track.assert_not_called()
    else:
        TrackCiTaskUseCase(repository).execute("sample", PR, HEAD)
        operation.track.assert_not_called()  # Replay: no new binding.
    operation.load_for_tracking.assert_called_once_with("sample")
    repository.transaction.assert_called_once_with()


def test_ci_tracking_use_case_creates_missing_binding():
    operation = Mock()
    operation.load_for_tracking.return_value = CiTrackingSnapshot(
        "READY_IN_REVIEW", REPO, HEAD, None, None
    )
    scope = MagicMock()
    scope.__enter__.return_value = operation
    repository = Mock(transaction=Mock(return_value=scope))
    TrackCiTaskUseCase(repository).execute("sample", PR, HEAD)
    operation.track.assert_called_once_with("sample", PR, HEAD)


def test_ci_reader_invalid_persisted_dto_is_corrupted_data():
    session = MagicMock()
    session.scalars.return_value.all.return_value = [
        SimpleNamespace(
            task_id="sample",
            pr_number="not-an-int",
            head_sha=HEAD,
            verdict="BLOCKED",
            repair_heads=0,
        )
    ]
    scope = MagicMock()
    scope.__enter__.return_value = session
    reader = SqlAlchemyCiStateReader(lambda: scope)

    with pytest.raises(
        CorruptedPersistenceDataError,
        match="^PostgreSQL Controller CI operation failed$",
    ):
        reader.tracked()
    session.commit.assert_not_called()


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
        authorize_task(factory, spec(task_id))
        try:
            yield config, factory, task_id, deliveries
        finally:
            with factory.transaction() as conn:
                for row in (ReviewAttempt, CIObservation, CITask, TaskEvent, Task):
                    conn.execute(delete(row).where(row.task_id == task_id))
                for row in (CIProcessedDelivery, WebhookInboxRow):
                    conn.execute(
                        delete(row).where(row.delivery_id.in_([first, second]))
                    )


@pytest.fixture
def mcp_provider_env(setup, monkeypatch):
    config, *_ = setup
    monkeypatch.setenv("PG_RUNTIME_DSN", config.pg_runtime_dsn)
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "1")
    monkeypatch.delenv("WFC_POC_NO_AUTH", raising=False)


def handoff(factory, task_id, head):
    record_handoff(
        factory, task_id, REPO, "task/test", BASE, head, "docs/handoff.md", "test"
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
    inbox = SqlAlchemyWebhookInboxRepository(session_scope=factory.get_scoped_session)
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


def test_authorized_binding_and_exact_identity(setup, mcp_provider_env):
    _, factory, task_id, _ = setup
    reader, repository = create_ci_state_adapters(factory)
    track = TrackCiTaskUseCase(repository).execute
    approved = CheckCiApprovalUseCase(reader).execute
    record = RecordCiEvidenceUseCase(repository).execute
    with pytest.raises(WorkflowError, match="authorized exact task HEAD"):
        track(task_id, PR, HEAD)
    handoff(factory, task_id, HEAD)
    for invalid_task, invalid_pr, invalid_head, error_type in [
        ("unknown", PR, HEAD, WorkflowError),
        (task_id, True, HEAD, InvalidApplicationRequestError),
        (task_id, PR, NEXT, WorkflowError),
        (task_id, PR, HEAD.upper(), InvalidApplicationRequestError),
    ]:
        with pytest.raises(error_type):
            track(invalid_task, invalid_pr, invalid_head)
    with factory.transaction() as conn:
        conn.execute(
            update(Task).where(Task.task_id == task_id).values(repository="other/repo")
        )
    with pytest.raises(WorkflowError, match="authorized exact task HEAD"):
        track(task_id, PR, HEAD)
    with factory.transaction() as conn:
        conn.execute(
            update(Task).where(Task.task_id == task_id).values(repository=REPO)
        )

    run_race(lambda: track(task_id, PR, HEAD), lambda: track(task_id, PR, HEAD))
    with pytest.raises(WorkflowError, match="PR differs"):
        track(task_id, PR + 1, HEAD)
    tracked = reader.tracked(limit=1)
    assert tracked == (CiTrackedTaskSnapshot(task_id, PR, HEAD, "BLOCKED", 0),)
    with pytest.raises(FrozenInstanceError):
        tracked[0].head_sha = NEXT
    for invalid_limit in (0, 33, True):
        with pytest.raises(ValueError, match="bounded CI task scan"):
            reader.tracked(invalid_limit)
    assert not approved(task_id, HEAD)
    assert not approved(task_id, "abbreviated")
    assert not approved(task_id, NEXT)

    valid = evidence(task_id)
    with pytest.raises(WorkflowError, match="untrusted CI evidence"):
        record(valid.as_dict())
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
            record(replace(valid, **overrides))
    assert observations(factory, task_id) == []
    record(valid)
    assert approved(task_id, HEAD)
    with factory.transaction() as conn:
        conn.execute(
            update(CIObservation)
            .where(CIObservation.task_id == task_id)
            .values(pr_number=PR + 1)
        )
    assert not approved(task_id, HEAD)
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
    assert not approved(task_id, HEAD)
    assert reader.tracked() == ()
    with (
        postgres_review_writes_provider() as (claim_use_case, _),
        pytest.raises(WorkflowError, match="verified exact-HEAD CI is required"),
    ):
        claim_use_case.execute(ClaimReviewInput(task_id, NEXT, "lead"))
    with pytest.raises(WorkflowError, match="HEAD differs"):
        track(task_id, PR, NEXT)
    with pytest.raises(WorkflowError, match="mismatched"):
        record(valid)
    assert len(observations(factory, task_id)) == 1


@pytest.mark.parametrize("ci_required", ["1", "0"], ids=["required", "disabled"])
def test_mcp_provider_claims_require_exact_head_ci(
    setup, mcp_provider_env, monkeypatch, ci_required
):
    _, factory, task_id, _ = setup
    handoff(factory, task_id, HEAD)
    reader, repository = create_ci_state_adapters(factory)
    track = TrackCiTaskUseCase(repository).execute
    approved = CheckCiApprovalUseCase(reader).execute
    record = RecordCiEvidenceUseCase(repository).execute
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", ci_required)
    assert not approved(task_id, HEAD)

    if ci_required == "1":
        with (
            postgres_review_writes_provider() as (claim_use_case, _),
            pytest.raises(WorkflowError, match="verified exact-HEAD CI is required"),
        ):
            claim_use_case.execute(ClaimReviewInput(task_id, HEAD, "lead"))
        track(task_id, PR, HEAD)
        record(evidence(task_id))
        assert approved(task_id, HEAD)

    with postgres_review_writes_provider() as (claim_use_case, _):
        with pytest.raises(WorkflowError, match="stale claim SHA"):
            claim_use_case.execute(ClaimReviewInput(task_id, NEXT, "lead"))
        claim = claim_use_case.execute(ClaimReviewInput(task_id, HEAD, "lead"))
    assert claim.task_id == task_id
    assert claim.reviewed_sha == HEAD


def test_concurrent_observations_keep_sticky_failure_and_history(setup):
    config, factory, task_id, _ = setup
    handoff(factory, task_id, HEAD)
    _, repository = create_ci_state_adapters(factory)
    track = TrackCiTaskUseCase(repository).execute
    record = RecordCiEvidenceUseCase(repository).execute
    track(task_id, PR, HEAD)
    failure = evidence(task_id, "FAILURE")
    success = replace(
        evidence(task_id), check_run_id=8, workflow_run_id=45, run_attempt=2
    )
    run_race(lambda: record(failure), lambda: record(success))

    with PostgresFactory.open(config) as restarted:
        reader, repository = create_ci_state_adapters(restarted)
        approved = CheckCiApprovalUseCase(reader).execute
        record = RecordCiEvidenceUseCase(repository).execute
        record(success)
        record(evidence(task_id, "BLOCKED"))
        assert reader.previous_failure(task_id, HEAD)
        assert not reader.previous_failure(task_id, NEXT)
        assert not approved(task_id, HEAD)
        assert reader.tracked()[0].verdict == "FAILURE"
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
    _, factory, task_id, _ = setup
    reader, repository = create_ci_state_adapters(factory)
    track = TrackCiTaskUseCase(repository).execute
    record = RecordCiEvidenceUseCase(repository).execute
    provide_handoff = PostgresHandoffProvider(factory, clock=lambda: 1000.0)

    def current_handoff(head):
        with provide_handoff() as operation:
            return operation.execute(
                HandoffRequest(
                    task_id, REPO, "task/test", BASE, head, "docs/handoff.md", "test"
                )
            )

    current_handoff(HEAD)
    track(task_id, PR, HEAD)
    record(evidence(task_id, "BLOCKED"))
    with pytest.raises(WorkflowError, match="repair"):
        current_handoff(NEXT)
    record(evidence(task_id, "FAILURE"))

    previous = HEAD
    for count, head in enumerate((NEXT, "c" * 40), 1):
        current_handoff(head)
        track(task_id, PR, head)
        assert reader.tracked() == (
            CiTrackedTaskSnapshot(task_id, PR, head, "BLOCKED", count),
        )
        with pytest.raises(WorkflowError, match="mismatched"):
            record(evidence(task_id, head=previous))
        record(evidence(task_id, "FAILURE", head))
        previous = head

    with pytest.raises(WorkflowError, match="stale"):
        current_handoff(HEAD)
    with pytest.raises(WorkflowError, match="repair"):
        current_handoff("d" * 40)
    assert reader.tracked()[0].repair_heads == 2
    assert [row["head_sha"] for row in observations(factory, task_id)] == [
        HEAD,
        HEAD,
        NEXT,
        previous,
    ]


@pytest.fixture
def failed_handoff(setup):
    config, factory, task_id, _ = setup
    provide_handoff = PostgresHandoffProvider(factory, clock=lambda: 1000.0)
    request = HandoffRequest(
        task_id, REPO, "task/test", BASE, HEAD, "docs/handoff.md", "test"
    )
    with provide_handoff() as operation:
        operation.execute(request)
    _, repository = create_ci_state_adapters(factory)
    TrackCiTaskUseCase(repository).execute(task_id, PR, HEAD)
    RecordCiEvidenceUseCase(repository).execute(evidence(task_id, "FAILURE"))
    return config, provide_handoff, request


def test_conflicting_repairs_commit_only_one_head(failed_handoff, monkeypatch):
    config, provide_handoff, request = failed_handoff
    sessions = []
    original = PostgresHandoffRepository.lock_task
    # Both transactions have started before either attempts to lock the task.
    acquired_sessions = Barrier(2, timeout=10)

    def observe_session(self, task_id):
        sessions.append(self._session)
        acquired_sessions.wait()
        return original(self, task_id)

    monkeypatch.setattr(PostgresHandoffRepository, "lock_task", observe_session)

    def propose(head):
        try:
            with provide_handoff() as operation:
                operation.execute(replace(request, new_head_sha=head))
        except WorkflowError as exc:
            assert "repair" in str(exc).lower()
            return
        winners.append(head)

    winners = []
    run_race(lambda: propose(NEXT), lambda: propose("c" * 40))
    assert len(winners) == 1
    assert len(sessions) == 2 and sessions[0] is not sessions[1]
    assert not any(session.in_transaction() for session in sessions)
    winner = winners[0]
    with PostgresFactory.open(config) as restarted:
        reader, _ = create_ci_state_adapters(restarted)
        assert reader.tracked() == (
            CiTrackedTaskSnapshot(request.task_id, PR, winner, "BLOCKED", 1),
        )
        with (
            restarted.get_scoped_session() as session,
            SqlAlchemyUnitOfWork(session, read_only=True),
        ):
            assert (
                session.scalar(
                    select(Task.current_head_sha).where(Task.task_id == request.task_id)
                )
                == winner
            )
            actions = list(
                session.scalars(
                    select(TaskEvent.action)
                    .where(TaskEvent.task_id == request.task_id)
                    .order_by(TaskEvent.event_id)
                )
            )
            assert actions == [
                "LOCAL_AUTHORIZATION",
                "HANDOFF",
                "CI_REPAIR_HANDOFF",
            ]


@pytest.mark.parametrize(
    ("failure_point", "error_type", "message"),
    [
        ("writes", RuntimeError, "synthetic repair write failure"),
        ("before_commit", TimeoutError, "dispatch deadline exceeded"),
        ("after_commit", TimeoutError, "dispatch deadline exceeded"),
    ],
)
def test_failed_repair_rolls_back_head_event_and_ci_binding(
    failed_handoff, monkeypatch, failure_point, error_type, message
):
    config, provider, request = failed_handoff
    provide_handoff = replace(provider, clock=lambda: 1001.0)
    original_record = PostgresHandoffRepository.record
    original_commit = Session.commit
    now = 0.0
    commits = []

    def record(self, *args):
        nonlocal now
        original_record(self, *args)
        if failure_point == "writes":
            raise RuntimeError("synthetic repair write failure")
        if failure_point == "before_commit":
            now = 2.0

    def commit(self):
        nonlocal now
        original_commit(self)
        commits.append(self)
        if failure_point == "after_commit":
            now = 2.0

    correction = replace(request, new_head_sha=NEXT)
    with monkeypatch.context() as patch:
        patch.setattr(
            budget, "time", SimpleNamespace(time=lambda: now, monotonic=lambda: now)
        )
        patch.setattr(PostgresHandoffRepository, "record", record)
        patch.setattr(Session, "commit", commit)
        with budget.dispatch_budget(1.0), pytest.raises(error_type, match=message):
            with provide_handoff() as operation:
                operation.execute(correction)
    assert len(commits) == (1 if failure_point == "after_commit" else 0)
    with PostgresFactory.open(config) as restarted:
        reader, _ = create_ci_state_adapters(restarted)
        assert reader.tracked() == (
            CiTrackedTaskSnapshot(request.task_id, PR, HEAD, "FAILURE", 0),
        )
        assert reader.previous_failure(request.task_id, HEAD)
        with (
            restarted.get_scoped_session() as session,
            SqlAlchemyUnitOfWork(session, read_only=True),
        ):
            task = session.execute(
                select(Task.current_head_sha, Task.status, Task.updated_at).where(
                    Task.task_id == request.task_id
                )
            ).one()
            assert tuple(task) == (
                HEAD,
                "READY_IN_REVIEW",
                1000.0,
            )
            actions = list(
                session.scalars(
                    select(TaskEvent.action)
                    .where(TaskEvent.task_id == request.task_id)
                    .order_by(TaskEvent.event_id)
                )
            )
            assert actions == [
                "LOCAL_AUTHORIZATION",
                "HANDOFF",
            ]
        with PostgresHandoffProvider(restarted, clock=lambda: 1001.0)() as retry:
            assert retry.execute(correction).head_sha == NEXT
        assert reader.tracked() == (
            CiTrackedTaskSnapshot(request.task_id, PR, NEXT, "BLOCKED", 1),
        )


def test_repair_with_unavailable_evidence_is_fail_closed_and_retryable(failed_handoff):
    _, provider, request = failed_handoff
    correction = replace(request, new_head_sha=NEXT)
    engine = provider.factory._engine

    def missing_column(_conn, _cursor, statement, parameters, _context, _executemany):
        if "controller_ci_observations" in statement and statement.endswith(
            "FOR UPDATE"
        ):
            return "SELECT missing_evidence_column", ()
        return statement, parameters

    event.listen(engine, "before_cursor_execute", missing_column, retval=True)
    try:
        with (
            provider() as operation,
            pytest.raises(WorkflowError, match="CI repair evidence is unavailable"),
        ):
            operation.execute(correction)
    finally:
        event.remove(engine, "before_cursor_execute", missing_column)

    reader, _ = create_ci_state_adapters(provider.factory)
    assert reader.tracked() == (
        CiTrackedTaskSnapshot(request.task_id, PR, HEAD, "FAILURE", 0),
    )
    with provider() as operation:
        assert operation.execute(correction).head_sha == NEXT


def test_reconcile_blocks_incomplete_or_synthetic_evidence_and_recovers(setup):
    config, factory, task_id, deliveries = setup
    handoff(factory, task_id, HEAD)
    reader, repository = create_ci_state_adapters(factory)
    track = TrackCiTaskUseCase(repository).execute
    approved = CheckCiApprovalUseCase(reader).execute
    track(task_id, PR, HEAD)
    delivery = deliveries[0]
    add_delivery(factory, delivery)

    class Checks(FakeChecks):
        phase = "missing"

        def list_check_runs(self, repo, sha):
            result = super().list_check_runs(repo, sha)
            if self.phase == "missing":
                return {"total_count": 0, "check_runs": []}
            if self.phase == "pending":
                result["check_runs"][0].update(status="in_progress", conclusion=None)
            if self.phase == "incomplete":
                result["total_count"] += 1
            if self.phase == "merge":
                result["check_runs"][0].update(event="pull_request", tested_sha=NEXT)
            return result

    checks = Checks(head=HEAD)
    with compose_ci_execution(CONTROLLER_CONFIG, config, checks=checks) as reconcile:
        for phase, reason in [
            ("missing", "REQUIRED_CHECK_MISSING"),
            ("pending", "CHECK_PENDING"),
            ("incomplete", "CHECK_HISTORY_INCOMPLETE"),
            ("merge", "REQUIRED_CHECK_MISSING"),
        ]:
            checks.phase = phase
            assert reconcile(CiRunRequest(startup=False)) == CiRunResponse(1, 1)
            assert observations(factory, task_id)[-1]["reason"] == reason
            assert reader.pending_events() == [delivery]
            assert not approved(task_id, HEAD)

    with PostgresFactory.open(config) as restarted:
        reader, _ = create_ci_state_adapters(restarted)
        approved = CheckCiApprovalUseCase(reader).execute
        checks = FakeChecks()
        with compose_ci_execution(
            CONTROLLER_CONFIG, config, checks=checks
        ) as reconcile:
            assert reconcile(CiRunRequest(startup=False)) == CiRunResponse(1, 0)
            calls = checks.calls
            assert reconcile(CiRunRequest(startup=False)) == CiRunResponse(0, 0)
            assert checks.calls == calls
        assert approved(task_id, HEAD)
        assert reader.pending_events() == []
        rows = observations(restarted, task_id)
        assert len(rows) == 5
        assert [row["verdict"] for row in rows] == [
            "BLOCKED",
            "BLOCKED",
            "BLOCKED",
            "BLOCKED",
            "SUCCESS",
        ]
        assert rows[-1]["reason"] == "REQUIRED_CHECK_PASSED"
        assert rows[-1]["observed_at"]
        assert (
            rows[-1]["check_run_id"],
            rows[-1]["workflow_run_id"],
            rows[-1]["run_attempt"],
        ) == (3, 44, 1)


@pytest.mark.parametrize(
    ("scenario", "reason"),
    [
        ("offline", "GITHUB_READ_UNAVAILABLE"),
        ("stale", "PR_HEAD_CHANGED"),
        ("failed", "REQUIRED_CHECK_FAILED"),
    ],
)
def test_reconcile_settles_failure_or_replays_retained_delivery(
    setup, scenario, reason
):
    config, factory, task_id, (delivery, _) = setup
    handoff(factory, task_id, HEAD)
    reader, repository = create_ci_state_adapters(factory)
    TrackCiTaskUseCase(repository).execute(task_id, PR, HEAD)
    add_delivery(factory, delivery)
    approved = CheckCiApprovalUseCase(reader).execute

    class Checks(FakeChecks):
        def list_check_runs(self, repo, sha):
            result = super().list_check_runs(repo, sha)
            if scenario == "failed":
                result["check_runs"][0]["conclusion"] = "failure"
            return result

    checks = Checks(
        unavailable=scenario == "offline", head=NEXT if scenario == "stale" else HEAD
    )
    with compose_ci_execution(CONTROLLER_CONFIG, config, checks=checks) as reconcile:
        assert reconcile(CiRunRequest(startup=False)) == CiRunResponse(
            1, int(scenario != "failed")
        )
        assert observations(factory, task_id)[-1]["reason"] == reason
        assert not approved(task_id, HEAD)
        if scenario == "failed":
            assert reader.pending_events() == []
            calls = checks.calls
            assert reconcile(CiRunRequest(startup=False)) == CiRunResponse(0, 0)
            assert checks.calls == calls
            return
        assert reader.pending_events() == [delivery]

    with compose_ci_execution(
        CONTROLLER_CONFIG, config, checks=FakeChecks()
    ) as reconcile:
        assert reconcile(CiRunRequest(startup=False)) == CiRunResponse(1, 0)
    assert observations(factory, task_id)[-1]["reason"] == "REQUIRED_CHECK_PASSED"
    assert approved(task_id, HEAD)
    assert reader.pending_events() == []


def test_pg_ci_entrypoint_reconciles_while_controller_holds_its_lock(setup):
    config, factory, task_id, deliveries = setup
    handoff(factory, task_id, HEAD)
    reader, repository = create_ci_state_adapters(factory)
    track = TrackCiTaskUseCase(repository).execute
    approved = CheckCiApprovalUseCase(reader).execute
    track(task_id, PR, HEAD)
    add_delivery(factory, deliveries[0])
    checks = FakeChecks(head=HEAD)

    with PostgresLeadershipLock(factory, LeadershipDomain.CONTROLLER) as controller:
        assert controller.try_acquire()
        with compose_ci_execution(
            CONTROLLER_CONFIG, config, checks=checks
        ) as reconcile:
            run_ci_once(reconcile)
        assert controller.is_held()
        with PostgresLeadershipLock(factory, LeadershipDomain.CI_WATCHER) as successor:
            assert successor.try_acquire()

    assert checks.calls > 0
    assert approved(task_id, HEAD)
    assert reader.pending_events() == []
    assert len(observations(factory, task_id)) == 1


def test_pg_ci_watch_two_passes_ack_once(setup):
    config, factory, task_id, deliveries = setup
    handoff(factory, task_id, HEAD)
    reader, repository = create_ci_state_adapters(factory)
    track = TrackCiTaskUseCase(repository).execute
    approved = CheckCiApprovalUseCase(reader).execute
    track(task_id, PR, HEAD)
    add_delivery(factory, deliveries[0])
    checks = FakeChecks(head=HEAD)
    sleeps = []

    with compose_ci_execution(CONTROLLER_CONFIG, config, checks=checks) as reconcile:
        run_ci_watch(reconcile, sleep=sleeps.append, stop=lambda: len(sleeps) == 2)

    assert sleeps == [2, 2]
    assert checks.calls > 0
    assert approved(task_id, HEAD)
    assert reader.pending_events() == []
    assert len(observations(factory, task_id)) == 1


def test_pg_ci_watch_denied_leader_keeps_inbox_pending(setup):
    config, factory, task_id, deliveries = setup
    handoff(factory, task_id, HEAD)
    reader, repository = create_ci_state_adapters(factory)
    track = TrackCiTaskUseCase(repository).execute
    approved = CheckCiApprovalUseCase(reader).execute
    track(task_id, PR, HEAD)
    add_delivery(factory, deliveries[0])
    checks = FakeChecks(head=HEAD)
    leader = PostgresLeadershipLock(factory, LeadershipDomain.CI_WATCHER)
    assert leader.try_acquire()
    try:
        with (
            pytest.raises(ApplicationError, match="leadership unavailable"),
            compose_ci_execution(CONTROLLER_CONFIG, config, checks=checks) as reconcile,
        ):
            run_ci_watch(
                reconcile, sleep=lambda _: pytest.fail("denied leader must not retry")
            )
    finally:
        leader.release()

    assert checks.calls == 0
    assert reader.pending_events() == [deliveries[0]]
    assert not approved(task_id, HEAD)
    assert observations(factory, task_id) == []


def test_pending_delivery_order_concurrent_settlement_and_fk(setup):
    config, factory, task_id, (first, second) = setup
    reader, repository = create_ci_state_adapters(factory)
    acknowledge = AcknowledgeCiEventUseCase(repository).execute
    add_delivery(factory, second)
    add_delivery(factory, first)
    assert reader.pending_events(limit=1) == [first]
    assert reader.pending_events() == [first, second]
    for invalid_limit in (0, 21, True):
        with pytest.raises(ValueError, match="bounded"):
            reader.pending_events(invalid_limit)
    with pytest.raises(WorkflowError, match="invalid"):
        acknowledge(first.delivery_id.upper())
    missing = str(uuid4())
    with pytest.raises(WorkflowError, match="unknown"):
        acknowledge(missing)
    with pytest.raises(IntegrityError) as fk:
        with factory.transaction() as conn:
            conn.execute(
                CIProcessedDelivery.__table__.insert().values(delivery_id=missing)
            )
    assert fk.value.orig.sqlstate == "23503"

    run_race(
        lambda: acknowledge(first.delivery_id),
        lambda: acknowledge(first.delivery_id),
    )
    with PostgresFactory.open(config) as restarted:
        reader, repository = create_ci_state_adapters(restarted)
        approved = CheckCiApprovalUseCase(reader).execute
        acknowledge = AcknowledgeCiEventUseCase(repository).execute
        assert reader.pending_events() == [second]
        acknowledge(first.delivery_id)
        with restarted.transaction() as conn:
            assert conn.execute(
                select(CIProcessedDelivery.delivery_id).where(
                    CIProcessedDelivery.delivery_id.in_(
                        [first.delivery_id, second.delivery_id]
                    )
                )
            ).scalars().all() == [first.delivery_id]
        assert not approved(task_id, HEAD)


def test_lost_watcher_session_keeps_real_inbox_pending(setup):
    config, factory, task_id, (notification, _) = setup
    handoff(factory, task_id, HEAD)
    reader, repository = create_ci_state_adapters(factory)
    track = TrackCiTaskUseCase(repository).execute
    approved = CheckCiApprovalUseCase(reader).execute
    track(task_id, PR, HEAD)
    add_delivery(factory, notification)
    entered = Event()
    resume = Event()
    checks = FakeChecks(head=HEAD)
    list_check_runs = checks.list_check_runs

    def blocked_request(repository, sha):
        entered.set()
        assert resume.wait(20)
        return list_check_runs(repository, sha)

    checks.list_check_runs = Mock(side_effect=blocked_request)

    def run_pass():
        with compose_ci_execution(
            CONTROLLER_CONFIG, config, checks=checks
        ) as reconcile:
            run_ci_once(reconcile)

    with (
        PostgresLeadershipLock(factory, LeadershipDomain.CONTROLLER) as controller,
        PostgresLeadershipLock(factory, LeadershipDomain.CI_WATCHER) as contender,
    ):
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run_pass)
            try:
                assert entered.wait(10)
                assert controller.try_acquire()
                assert not contender.try_acquire()
                with factory.transaction() as connection:
                    pid = connection.execute(
                        text(
                            """
                            SELECT locks.pid
                            FROM pg_catalog.pg_locks AS locks
                            JOIN pg_catalog.pg_stat_activity AS activity USING (pid)
                            WHERE locks.locktype = 'advisory'
                              AND locks.classid = :namespace
                              AND locks.objid = :domain
                              AND locks.objsubid = 2
                              AND locks.mode = 'ExclusiveLock'
                              AND locks.granted
                              AND activity.datname = current_database()
                              AND activity.usename = current_user
                            """
                        ),
                        {
                            "namespace": _WORKFLOW_NAMESPACE,
                            "domain": int(LeadershipDomain.CI_WATCHER),
                        },
                    ).scalar_one()
                    assert (
                        connection.scalar(
                            text("SELECT pg_catalog.pg_terminate_backend(:pid, 5000)"),
                            {"pid": pid},
                        )
                        is True
                    )
            finally:
                resume.set()
            with pytest.raises(PermissionError, match="leadership lost"):
                future.result(timeout=10)
        assert controller.is_held()
        with PostgresLeadershipLock(factory, LeadershipDomain.CI_WATCHER) as successor:
            assert successor.try_acquire()

    checks.list_check_runs.assert_called_once_with(REPO, HEAD)
    assert checks.calls == 2  # Initial PR read and the blocked Checks read.
    assert observations(factory, task_id) == []
    assert not approved(task_id, HEAD)
    assert reader.pending_events() == [notification]
    with factory.transaction() as connection:
        assert (
            connection.scalar(
                select(CIProcessedDelivery.delivery_id).where(
                    CIProcessedDelivery.delivery_id == notification.delivery_id
                )
            )
            is None
        )


def test_verdict_write_failure_rolls_back_already_inserted_observation(setup):
    config, factory, task_id, _ = setup
    handoff(factory, task_id, HEAD)
    reader, repository = create_ci_state_adapters(factory)
    track = TrackCiTaskUseCase(repository).execute
    approved = CheckCiApprovalUseCase(reader).execute
    record = RecordCiEvidenceUseCase(repository).execute
    track(task_id, PR, HEAD)
    record(evidence(task_id, "BLOCKED"))
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
            PersistenceUnavailableError,
            match="^PostgreSQL Controller CI operation failed$",
        ) as error:
            RecordCiEvidenceUseCase(
                SqlAlchemyCiStateRepository(failing.get_scoped_session)
            ).execute(evidence(task_id))
        assert type(error.value) is PersistenceUnavailableError
        assert error.value.__cause__ is None

    assert attempted == [2]
    assert [row["verdict"] for row in observations(factory, task_id)] == ["BLOCKED"]
    assert reader.tracked()[0].verdict == "BLOCKED"
    assert not approved(task_id, HEAD)
    record(evidence(task_id))
    assert approved(task_id, HEAD)


def test_ci_reader_sessions_and_repository_commit_rollback_retry(setup):
    config, factory, task_id, (notification, _) = setup
    handoff(factory, task_id, HEAD)
    sessions = []

    @contextmanager
    def session_scope():
        with factory.get_scoped_session() as session:
            sessions.append(session)
            yield session

    reader = SqlAlchemyCiStateReader(session_scope)
    repository = SqlAlchemyCiStateRepository(session_scope)
    track = TrackCiTaskUseCase(repository).execute
    approved = CheckCiApprovalUseCase(reader).execute
    record = RecordCiEvidenceUseCase(repository).execute
    acknowledge = AcknowledgeCiEventUseCase(repository).execute
    track(task_id, PR, HEAD)
    add_delivery(factory, notification)

    commits, rollbacks = [], []
    engine = factory._engine
    event.listen(engine, "commit", lambda _conn: commits.append("commit"))
    event.listen(engine, "rollback", lambda _conn: rollbacks.append("rollback"))
    assert reader.tracked() == (CiTrackedTaskSnapshot(task_id, PR, HEAD, "BLOCKED", 0),)
    assert not reader.previous_failure(task_id, HEAD)
    assert not approved(task_id, HEAD)
    assert reader.pending_events() == [notification]
    assert commits == []
    assert len(rollbacks) == 4

    should_fail = True

    def fail_commit(connection):
        nonlocal should_fail
        if should_fail:
            should_fail = False
            connection.execute(text("SELECT 1 / 0"))

    event.listen(engine, "commit", fail_commit)
    with pytest.raises(PersistenceUnavailableError, match="Controller CI operation"):
        record(evidence(task_id))
    event.remove(engine, "commit", fail_commit)
    assert observations(factory, task_id) == []
    assert reader.tracked()[0].verdict == "BLOCKED"

    # The retry commits before returning and is visible through another Session.
    commits.clear()

    def assert_write_settings(connection):
        settings = connection.execute(
            text(
                "SELECT current_setting('transaction_isolation'), "
                "current_setting('lock_timeout'), "
                "current_setting('statement_timeout'), "
                "current_setting('synchronous_commit')"
            )
        ).one()
        assert settings == ("read committed", "5s", "5s", "on")

    event.listen(engine, "commit", assert_write_settings)
    try:
        record(evidence(task_id))
        assert commits == ["commit"]
        assert approved(task_id, HEAD)
        acknowledge(notification.delivery_id)
        assert commits == ["commit", "commit"]
    finally:
        event.remove(engine, "commit", assert_write_settings)
    assert reader.pending_events() == []
    assert len({id(session) for session in sessions}) == len(sessions)
