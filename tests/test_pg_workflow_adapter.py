"""Live PostgreSQL workflow contract checks on a disposable database."""

import asyncio
import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.fastmcp.exceptions import ToolError
from pg_task_fixtures import authorize_task, record_handoff
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from test_agent_workflow import BASE, HEAD, NEXT, REPO, payload, spec

from agent_workflow import budget
from agent_workflow.app_config import AppConfig
from agent_workflow.application.exceptions import PersistenceUnavailableError
from agent_workflow.application.review.use_cases.claim_review import ClaimReviewUseCase
from agent_workflow.application.review.use_cases.get_review_context import (
    GetReviewContextInput,
)
from agent_workflow.application.review.use_cases.submit_review import (
    SubmitReviewUseCase,
)
from agent_workflow.application.review.use_cases.write_contracts import ClaimReviewInput
from agent_workflow.budget import dispatch_budget
from agent_workflow.composition.postgres_mcp import (
    postgres_mcp_review_writes,
    postgres_mcp_task_operations,
)
from agent_workflow.composition.review_deps import create_get_review_context_use_case
from agent_workflow.composition.task_handoff_deps import PostgresHandoffProvider
from agent_workflow.infrastructure.database.models import CITask, ReviewFinding, Task
from agent_workflow.infrastructure.database.readers.review_queue_reader import (
    PostgresReviewQueueReader,
)
from agent_workflow.infrastructure.database.repositories.review_writes import (
    PostgresReviewWrites,
)
from agent_workflow.infrastructure.database.repositories.task_handoff import (
    PostgresHandoffRepository,
)
from agent_workflow.infrastructure.database.session_factory import PostgresFactory
from agent_workflow.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from agent_workflow.integrations.github import WorkflowError
from agent_workflow.interfaces.mcp.review_request import Submission
from agent_workflow.interfaces.review_assignment_response import review_context_response
from agent_workflow.mcp.server import mcp
from agent_workflow.storage.postgres_workflow import PostgresWorkflowRepository


@pytest.mark.parametrize("require_ci", [True, False])
def test_mcp_composition_owns_factory_and_config(require_ci):
    config = AppConfig(
        pg_runtime_dsn="postgresql+psycopg://user:password@localhost/test",
        ci_gate_required=require_ci,
    )
    factory = Mock()
    with patch.object(PostgresFactory, "open", return_value=factory) as open_factory:
        with postgres_mcp_task_operations(config) as (handoff, assignment):
            open_factory.assert_called_once_with(config)
            assert isinstance(handoff, PostgresHandoffProvider)
            assert handoff.factory is factory
            factory.get_scoped_session.assert_not_called()
            assert assignment.context is not None
            factory.close.assert_not_called()
        factory.close.assert_called_once_with()


def test_mcp_composition_closes_on_scope_and_construction_errors():
    config = AppConfig(
        pg_runtime_dsn="postgresql+psycopg://user:password@localhost/test"
    )
    factory = Mock()
    with patch.object(PostgresFactory, "open", return_value=factory):
        with pytest.raises(RuntimeError, match="scope error"):
            with postgres_mcp_task_operations(config):
                raise RuntimeError("scope error")
        factory.close.assert_called_once_with()
        factory.reset_mock()
        with patch(
            "agent_workflow.composition.postgres_mcp.PostgresHandoffProvider",
            side_effect=RuntimeError("construction error"),
        ):
            with pytest.raises(RuntimeError, match="construction error"):
                with postgres_mcp_task_operations(config):
                    pytest.fail("service must not be yielded")
        factory.close.assert_called_once_with()


def test_review_write_composition_shares_one_fresh_scoped_session_per_invocation():
    config = AppConfig(
        pg_runtime_dsn="postgresql+psycopg://user:password@localhost/test",
        ci_gate_required=True,
    )
    factories = [MagicMock(spec=PostgresFactory) for _ in range(2)]
    sessions = [Mock(), Mock()]
    for factory, session in zip(factories, sessions, strict=True):
        factory.__enter__.return_value = factory
        factory.get_scoped_session.return_value.__enter__.return_value = session

    with patch.object(PostgresFactory, "open", side_effect=factories) as open_factory:
        with postgres_mcp_review_writes(config) as first:
            claim, submit = first
            assert claim.uow is submit.uow
            assert claim.repository is submit.repository
            assert claim.uow._session is claim.repository._session is sessions[0]
            assert claim.require_ci is submit.require_ci is True
            assert sessions[0].mock_calls == []
        with pytest.raises(RuntimeError, match="tool failed"):
            with postgres_mcp_review_writes(config) as (claim, submit):
                assert claim.uow is submit.uow and claim.uow is not first[0].uow
                assert claim.repository is submit.repository
                assert claim.repository is not first[0].repository
                assert claim.uow._session is claim.repository._session is sessions[1]
                raise RuntimeError("tool failed")

    assert open_factory.call_count == 2
    for factory in factories:
        factory.get_scoped_session.assert_called_once_with()
        factory.get_scoped_session.return_value.__exit__.assert_called_once()
        factory.__exit__.assert_called_once()


@pytest.mark.parametrize("dsn", [None, "sqlite:///fallback.db", "invalid"])
def test_mcp_composition_rejects_missing_or_invalid_dsn(dsn):
    config = AppConfig.model_construct(pg_runtime_dsn=dsn)
    with patch(
        "agent_workflow.infrastructure.database.session_factory.create_engine"
    ) as engine:
        with pytest.raises(ValueError, match="PG_RUNTIME_DSN"):
            with postgres_mcp_task_operations(config):
                pytest.fail("operation must not be yielded")
        engine.assert_not_called()


@pytest.fixture
def setup():
    dsn = os.environ.get("PG_TEST_DSN")
    if not dsn:
        pytest.skip("disposable PG_TEST_DSN not supplied")
    task_id = "PG02B-" + uuid.uuid4().hex
    with PostgresFactory.open(AppConfig(pg_runtime_dsn=dsn)) as factory:
        repo = PostgresWorkflowRepository(factory)
        try:
            yield repo, task_id, factory
        finally:
            with factory.transaction() as conn:
                for table in (
                    "review_findings",
                    "reviews",
                    "review_attempts",
                    "task_events",
                    "controller_ci_observations",
                    "controller_ci_tasks",
                    "tasks",
                ):
                    conn.execute(
                        text(f"DELETE FROM {table} WHERE task_id=:task_id"),
                        {"task_id": task_id},
                    )


def ready(repo, task_id):
    authorize_task(repo.factory, spec(task_id))
    record_handoff(
        repo.factory, task_id, REPO, "task/test", BASE, HEAD, "docs/handoff.md", "test"
    )


def claim_task(
    repo, task_id, head, reviewer, *, require_ci=False, clock=None, lease=900
):
    with repo.factory.get_scoped_session() as session:
        operation = ClaimReviewUseCase(
            SqlAlchemyUnitOfWork(session),
            PostgresReviewWrites(session),
            clock=clock or (lambda: 1000.0),
            token=lambda: uuid.uuid4().hex,
            require_ci=require_ci,
            lease_seconds=lease,
        )
        return asdict(operation.execute(ClaimReviewInput(task_id, head, reviewer)))


def submit_task(repo, data, reviewer, *, clock=None, require_ci=False):
    with repo.factory.get_scoped_session() as session:
        operation = SubmitReviewUseCase(
            SqlAlchemyUnitOfWork(session),
            PostgresReviewWrites(session),
            clock=clock or (lambda: 1000.0),
            token=lambda: uuid.uuid4().hex,
            require_ci=require_ci,
        )
        return asdict(
            operation.execute(Submission.model_validate(data).to_input(reviewer))
        )


def read_context(repo, task_id):
    operation = create_get_review_context_use_case(repo.factory)
    return review_context_response(operation.execute(GetReviewContextInput(task_id)))


@pytest.fixture
def mcp_postgres(setup, monkeypatch):
    monkeypatch.setenv("PG_RUNTIME_DSN", os.environ["PG_TEST_DSN"])
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "1")
    monkeypatch.setenv("WFC_AUTH_MODE", "auth0")
    monkeypatch.setenv("WFC_AUTH0_ALLOWED_SUB", "auth0|pg-test")
    monkeypatch.setenv("WFC_AUTH0_SCOPE", "wfc:workflow")

    @contextmanager
    def authorize():
        token = AccessToken(
            token="synthetic",
            client_id="test",
            scopes=["wfc:workflow"],
            subject="auth0|pg-test",
        )
        reset = auth_context_var.set(AuthenticatedUser(token))
        try:
            yield
        finally:
            auth_context_var.reset(reset)

    def invoke(name, arguments):
        with authorize():
            result = asyncio.run(mcp.call_tool(name, arguments))
        return json.loads(result[0].text)

    return invoke


def test_mcp_handoff_persists_across_tool_invocations(setup, mcp_postgres):
    repo, task_id, _ = setup
    authorize_task(repo.factory, spec(task_id))
    assert read_context(repo, task_id)["current_head_sha"] is None
    handoff = mcp_postgres(
        "register_task_handoff",
        {
            "task_id": task_id,
            "repository": REPO,
            "branch": "task/test",
            "base_sha": BASE,
            "new_head_sha": HEAD,
            "handoff_file_path": "docs/handoff.md",
            "test_summary": "PostgreSQL MCP fixture",
        },
    )
    context = mcp_postgres("get_review_context", {"task_id": task_id})
    assert handoff == {
        "task_id": task_id,
        "status": "READY_IN_REVIEW",
        "head_sha": HEAD,
    }
    assert context["task_id"] == task_id
    assert context["current_head_sha"] == HEAD
    assert context["status"] == "READY_IN_REVIEW"
    assert context["events"][-1]["action"] == "HANDOFF"
    with repo.read() as unit:
        assert unit.find_head_handoff(task_id, HEAD)["metadata"] == (
            '{"handoff_file_path":"docs/handoff.md",'
            '"test_summary":"PostgreSQL MCP fixture",'
            '"verification":"reporter_supplied_not_remotely_verified"}'
        )
    assert context["github_references"]["complete_diff"].endswith(BASE + "..." + HEAD)


def test_mcp_handoff_idempotent_and_empty_assignment(setup, mcp_postgres):
    repo, task_id, _ = setup
    authorize_task(repo.factory, spec(task_id))
    arguments = {
        "task_id": task_id,
        "repository": REPO,
        "branch": "task/test",
        "base_sha": BASE,
        "new_head_sha": HEAD,
        "handoff_file_path": "docs/handoff.md",
        "test_summary": "fixture",
    }
    assert mcp_postgres("register_task_handoff", arguments)["head_sha"] == HEAD
    assert mcp_postgres("register_task_handoff", arguments)["head_sha"] == HEAD
    assert (
        mcp_postgres("get_coder_assignment", {"task_id": task_id})[
            "unresolved_findings"
        ]
        == []
    )
    with repo.read() as unit:
        assert (
            len([e for e in unit.list_events((task_id,)) if e["action"] == "HANDOFF"])
            == 1
        )


def test_mcp_assignment_preserves_unresolved_findings(setup, mcp_postgres):
    repo, task_id, _ = setup
    ready(repo, task_id)
    claim = claim_task(repo, task_id, HEAD, "lead")
    submit_task(repo, payload(claim, "CHANGES_REQUESTED"), "lead")
    actual = mcp_postgres("get_coder_assignment", {"task_id": task_id})
    assert actual["status"] == "CHANGES_REQUESTED"
    assert actual["current_head_sha"] == HEAD
    assert len(actual["unresolved_findings"]) == 1
    assert actual["unresolved_findings"][0]["finding_id"] == "F-1"
    assert actual["unresolved_findings"][0]["resolution_status"] == "NOT_VERIFIED"


def test_handoff_write_failure_rolls_back_head_and_event(setup, monkeypatch):
    repo, task_id, _ = setup
    authorize_task(repo.factory, spec(task_id))
    original = PostgresHandoffRepository.record

    def fail_after_writes(self, *args):
        original(self, *args)
        raise RuntimeError("synthetic write failure")

    monkeypatch.setattr(PostgresHandoffRepository, "record", fail_after_writes)
    with pytest.raises(RuntimeError, match="synthetic write failure"):
        record_handoff(
            repo.factory,
            task_id,
            REPO,
            "task/test",
            BASE,
            HEAD,
            "docs/handoff.md",
            "fixture",
        )
    with repo.read() as unit:
        task = unit.find_task((task_id,))
        assert task["current_head_sha"] is None
        assert task["status"] == "AWAITING_HANDOFF"
        assert [event["action"] for event in unit.list_events((task_id,))] == [
            "LOCAL_AUTHORIZATION"
        ]


@pytest.mark.parametrize("verdict", ["APPROVED", "ESCALATED"])
def test_mcp_context_and_result_preserve_persisted_history(
    setup, mcp_postgres, verdict
):
    repo, task_id, _ = setup
    arguments = {"task_id": task_id}
    authorize_task(
        repo.factory, {**spec(task_id), "context_paths": ["docs/review context.md"]}
    )

    def assert_history(status, head, rounds):
        context = mcp_postgres("get_review_context", arguments)
        result = mcp_postgres("get_review_result", arguments)
        assert context["status"] == status
        assert context["current_head_sha"] == head
        assert context["completed_review_rounds"] == rounds
        assert len(context["previous_reviews"]) == rounds
        assert result["status"] == status
        assert result["previous_reviews"] == context["previous_reviews"]
        assert result["latest_reviewed_sha"] == (head if rounds else None)
        assert result["approval_current"] is None
        assert result["remote_verification"] == "not_performed_by_wfc"
        if head:
            assert context["github_references"]["context_files"] == {
                "docs/review context.md": f"https://github.com/{REPO}/blob/{head}/docs/review%20context.md"
            }

    assert_history("AWAITING_HANDOFF", None, 0)
    for sha, outcome, resolutions in (
        (HEAD, "CHANGES_REQUESTED", {}),
        (NEXT, verdict, {"F-1": "FIXED" if verdict == "APPROVED" else "NOT_FIXED"}),
    ):
        record_handoff(
            repo.factory,
            task_id,
            REPO,
            "task/test",
            BASE,
            sha,
            "docs/review context.md",
            "test",
        )
        claim = claim_task(repo, task_id, sha, "lead")
        submit_task(
            repo,
            payload(
                claim,
                outcome,
                prior_resolutions=resolutions,
                **({"findings": []} if sha == NEXT else {}),
            ),
            "lead",
        )
        assert_history(outcome, sha, 1 if sha == HEAD else 2)

    for name in ("get_review_context", "get_review_result"):
        with pytest.raises(ToolError, match="unknown authorized task"):
            mcp_postgres(name, {"task_id": f"{task_id}-missing"})


def test_mcp_queue_filters_and_recovers_a_lease_in_the_workflow_transaction(
    setup,
    mcp_postgres,
):
    repo, task_id, factory = setup
    ready(repo, task_id)
    claim = claim_task(repo, task_id, HEAD, "lead")
    assert read_context(repo, task_id)["status"] == "IN_REVIEW"

    # Even an effort filter with no matches recovers stale leases.
    assert mcp_postgres("list_review_queue", {"requested_effort": "invalid"}) == {
        "tasks": []
    }
    with repo.read() as unit:
        assert unit.find_claim((claim["claim_token"], task_id))["status"] == "EXPIRED"
    assert read_context(repo, task_id)["events"][-1]["action"] == "LEASE_EXPIRED"

    assert mcp_postgres("list_review_queue", {}) == {"tasks": []}
    with factory.transaction() as conn:
        conn.execute(
            text(
                "INSERT INTO controller_ci_tasks (task_id, pr_number, head_sha, verdict)"
                " VALUES (:id, 7, :sha, 'SUCCESS')"
            ),
            {"id": task_id, "sha": NEXT},
        )
    assert mcp_postgres("list_review_queue", {}) == {"tasks": []}
    with factory.transaction() as conn:
        conn.execute(
            text("UPDATE controller_ci_tasks SET head_sha=:sha WHERE task_id=:id"),
            {"id": task_id, "sha": HEAD},
        )
    assert mcp_postgres("list_review_queue", {"requested_effort": "high"}) == {
        "tasks": []
    }
    assert mcp_postgres("list_review_queue", {"requested_effort": "medium"}) == {
        "tasks": [
            {
                "task_id": task_id,
                "title": spec(task_id)["title"],
                "repository": REPO,
                "branch": "task/test",
                "head_sha": HEAD,
                "requested_effort": "medium",
                "review_round": 1,
            }
        ]
    }


def test_mcp_queue_read_failure_rolls_back_lease_recovery(
    setup, mcp_postgres, monkeypatch
):
    repo, task_id, _ = setup
    ready(repo, task_id)
    claim = claim_task(repo, task_id, HEAD, "lead")

    def fail_read(self, requested_effort):
        raise RuntimeError("synthetic queue read failure")

    monkeypatch.setattr(PostgresReviewQueueReader, "read", fail_read)
    with pytest.raises(ToolError, match="synthetic queue read failure"):
        mcp_postgres("list_review_queue", {})

    with repo.read() as unit:
        assert unit.find_claim((claim["claim_token"], task_id))["status"] == "ACTIVE"
    context = read_context(repo, task_id)
    assert context["status"] == "IN_REVIEW"
    assert context["events"][-1]["action"] == "CLAIM"


def test_rollback_and_constraints(setup):
    repo, task_id, _ = setup
    with pytest.raises(RuntimeError):
        with repo.transaction() as db:
            db.insert_task(
                (
                    task_id,
                    "{}",
                    REPO,
                    "branch",
                    BASE,
                    "medium",
                    0,
                    "AWAITING_HANDOFF",
                    1.0,
                    1.0,
                )
            )
            db.insert_event(
                (task_id, None, "AWAITING_HANDOFF", "CREATE", None, 1.0, "{}")
            )
            raise RuntimeError("rollback")
    with repo.read() as db:
        assert db.find_task((task_id,)) is None
    ready(repo, task_id)
    with pytest.raises(IntegrityError):
        with repo.transaction() as db:
            db.set_head((NEXT, task_id))
            db.insert_claim(("one", task_id, HEAD, "lead", 1.0, 2.0))
            db.insert_claim(("two", task_id, HEAD, "lead", 1.0, 2.0))
    with repo.read() as db:
        assert db.find_task((task_id,))["current_head_sha"] == HEAD
        assert db.find_active_claim((task_id,)) is None


def test_read_keeps_repeatable_read_snapshot_and_rejects_writes(setup):
    repo, task_id, factory = setup
    ready(repo, task_id)
    with repo.read() as db:
        assert (
            db.session.scalar(text("SHOW transaction_isolation")) == "repeatable read"
        )
        assert db.session.scalar(text("SHOW transaction_read_only")) == "on"
        assert db.find_task((task_id,))["current_head_sha"] == HEAD
        with factory.transaction() as connection:
            connection.execute(
                text("UPDATE tasks SET current_head_sha=:sha WHERE task_id=:task_id"),
                {"sha": NEXT, "task_id": task_id},
            )
        assert db.find_task((task_id,))["current_head_sha"] == HEAD
    with pytest.raises(DBAPIError):
        with repo.read() as db:
            db.session.execute(
                text("UPDATE tasks SET current_head_sha=:sha WHERE task_id=:task_id"),
                {"sha": HEAD, "task_id": task_id},
            )
    with repo.read() as db:
        assert db.find_task((task_id,))["current_head_sha"] == NEXT


@pytest.mark.parametrize("failed_check", [1, 2])
def test_deadline_before_outer_commit_rolls_back(setup, monkeypatch, failed_check):
    repo, task_id, factory = setup
    checks = 0

    def fail_at_deadline():
        nonlocal checks
        checks += 1
        if checks == failed_check:
            raise TimeoutError("deadline expired")

    with monkeypatch.context() as patch:
        patch.setattr(
            "agent_workflow.storage.postgres_workflow.check_deadline",
            fail_at_deadline,
        )
        with pytest.raises(TimeoutError, match="deadline expired"):
            with repo.transaction() as db:
                db.insert_task(
                    (
                        task_id,
                        "{}",
                        REPO,
                        "branch",
                        BASE,
                        "medium",
                        0,
                        "AWAITING_HANDOFF",
                        1.0,
                        1.0,
                    )
                )
    assert checks == failed_check
    with factory.transaction() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM tasks WHERE task_id=:task_id"),
                {"task_id": task_id},
            )
            == 0
        )


def test_competing_claims_and_ci_fail_closed(setup):
    repo, task_id, _ = setup
    ready(repo, task_id)
    with pytest.raises(WorkflowError, match="CI"):
        claim_task(repo, task_id, HEAD, "lead", require_ci=True)

    start_together = Barrier(2)

    def claim():
        start_together.wait(timeout=5)
        try:
            return claim_task(repo, task_id, HEAD, "lead")
        except WorkflowError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: claim(), range(2)))
    assert sum(c is not None for c in claims) == 1
    with repo.read() as db:
        assert db.find_active_claim((task_id,)) is not None
        assert not db.ci_approved((task_id, NEXT))


def test_claim_ci_query_failure_rolls_back_savepoint_and_fails_closed(
    setup, monkeypatch
):
    repo, task_id, _ = setup
    ready(repo, task_id)
    original = PostgresReviewWrites.has_successful_ci

    def query_with_unavailable_evidence(self, task_id, sha):
        def fail_query(state):
            if state.statement.column_descriptions[0]["entity"] is CITask:
                state.session.connection().execute(text("SELECT 1 / 0"))

        event.listen(self._session, "do_orm_execute", fail_query)
        try:
            assert original(self, task_id, sha) is False
            assert not self._session.in_nested_transaction()
            assert (
                self._session.scalar(
                    select(Task.task_id).where(Task.task_id == task_id)
                )
                == task_id
            )
            return False
        finally:
            event.remove(self._session, "do_orm_execute", fail_query)

    monkeypatch.setattr(
        PostgresReviewWrites, "has_successful_ci", query_with_unavailable_evidence
    )
    with pytest.raises(WorkflowError, match="verified exact-HEAD CI"):
        claim_task(repo, task_id, HEAD, "lead", require_ci=True)
    with repo.read() as unit:
        assert unit.find_active_claim((task_id,)) is None
        assert unit.find_task((task_id,))["status"] == "READY_IN_REVIEW"


def test_ci_repair_requires_matching_failure_evidence(setup, mcp_postgres, monkeypatch):
    repo, task_id, factory = setup
    ready(repo, task_id)
    with factory.transaction() as conn:
        conn.execute(
            text(
                "INSERT INTO controller_ci_tasks(task_id,pr_number,head_sha,verdict) VALUES (:id,7,:head,'FAILURE')"
            ),
            {"id": task_id, "head": HEAD},
        )
    with pytest.raises(WorkflowError, match="not authorized"):
        record_handoff(
            repo.factory,
            task_id,
            REPO,
            "task/test",
            BASE,
            NEXT,
            "docs/handoff.md",
            "repair",
        )
    with factory.transaction() as conn:
        conn.execute(
            text(
                "INSERT INTO controller_ci_observations(task_id,pr_number,head_sha,verdict,reason,check_run_id,workflow_run_id,run_attempt,observed_at) VALUES (:id,7,:head,'FAILURE','REQUIRED_CHECK_FAILED',1,2,1,'now')"
            ),
            {"id": task_id, "head": HEAD},
        )
    record_handoff(
        repo.factory,
        task_id,
        REPO,
        "task/test",
        BASE,
        NEXT,
        "docs/handoff.md",
        "repair",
    )
    with repo.read() as db:
        assert not db.ci_approved((task_id, NEXT))
        assert db.find_head_handoff(task_id, NEXT)["action"] == "CI_REPAIR_HANDOFF"
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "0")
    assert mcp_postgres("list_review_queue", {}) == {"tasks": []}
    with pytest.raises(WorkflowError, match="CI"):
        claim_task(repo, task_id, NEXT, "lead")
    with factory.transaction() as conn:
        conn.execute(
            text("UPDATE controller_ci_tasks SET verdict='SUCCESS' WHERE task_id=:id"),
            {"id": task_id},
        )
    with repo.read() as db:
        assert db.ci_approved((task_id, NEXT))
        assert not db.ci_approved((task_id, HEAD))
    queued = mcp_postgres("list_review_queue", {})["tasks"]
    assert [item["task_id"] for item in queued] == [task_id]
    assert queued[0]["head_sha"] == NEXT
    assert claim_task(repo, task_id, NEXT, "lead")["reviewed_sha"] == NEXT


def test_expired_claim_recovery(setup):
    repo, task_id, _ = setup
    now = [1000.0]
    ready(repo, task_id)
    first = claim_task(repo, task_id, HEAD, "lead", clock=lambda: now[0], lease=10)
    now[0] += 11
    second = claim_task(repo, task_id, HEAD, "lead", clock=lambda: now[0], lease=10)
    with pytest.raises(WorkflowError, match="expired"):
        submit_task(repo, payload(first), "lead", clock=lambda: now[0])
    assert second["claim_token"] != first["claim_token"]
    assert second["review_round"] == 1


def test_reclaim_rolls_back_expiry_on_rejection_and_write_failure(setup, monkeypatch):
    repo, task_id, _ = setup
    ready(repo, task_id)
    first = claim_task(repo, task_id, HEAD, "lead", lease=10)
    old_token = first["claim_token"]
    with repo.read() as unit:
        original_events = unit.list_events((task_id,))

    def expired_clock():
        return 1011.0

    def assert_old_lease_preserved():
        with repo.read() as unit:
            assert unit.find_active_claim((task_id,))["token"] == old_token
            assert unit.find_task((task_id,))["status"] == "IN_REVIEW"
            assert unit.list_events((task_id,)) == original_events

    with pytest.raises(WorkflowError, match="stale claim SHA"):
        claim_task(repo, task_id, NEXT, "lead", clock=expired_clock)
    assert_old_lease_preserved()

    original = PostgresReviewWrites.claim

    def fail_after_replacement(self, *args):
        original(self, *args)
        raise RuntimeError("injected claim failure")

    with monkeypatch.context() as patch:
        patch.setattr(PostgresReviewWrites, "claim", fail_after_replacement)
        with pytest.raises(RuntimeError, match="injected claim failure"):
            claim_task(repo, task_id, HEAD, "lead", clock=expired_clock)
    assert_old_lease_preserved()

    new_claim = claim_task(repo, task_id, HEAD, "lead", clock=expired_clock)
    assert new_claim["claim_token"] != old_token
    assert new_claim["review_round"] == 1
    with repo.read() as unit:
        assert unit.find_claim((old_token, task_id))["status"] == "EXPIRED"
        assert unit.find_active_claim((task_id,))["token"] == new_claim["claim_token"]
        transitions = unit.list_events((task_id,))[len(original_events) :]
        assert [
            (entry["action"], entry["previous_status"], entry["next_status"])
            for entry in transitions
        ] == [
            ("LEASE_EXPIRED", "IN_REVIEW", "READY_IN_REVIEW"),
            ("CLAIM", "READY_IN_REVIEW", "IN_REVIEW"),
        ]


def test_concurrent_identical_authorization_is_idempotent(setup):
    repo, task_id, _ = setup
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: authorize_task(repo.factory, spec(task_id)), range(2)))
    assert len(read_context(repo, task_id)["events"]) == 1


def test_adversarial_text_is_stored_as_data(setup):
    repo, task_id, _ = setup
    ready(repo, task_id)
    attack = "lead' OR 1=1 --"
    claim = claim_task(repo, task_id, HEAD, attack)
    submit_task(repo, payload(claim, summary=attack), attack)
    context = read_context(repo, task_id)
    assert context["previous_reviews"][0]["summary"] == attack
    with repo.read() as db:
        assert db.find_claim(("' OR 1=1 --", task_id)) is None
        assert len(db.list_reviews((task_id,))) == 1


def test_row_lock_timeout_rolls_back_and_retry_succeeds(setup):
    repo, task_id, factory = setup
    ready(repo, task_id)
    rolled_back_id = task_id + "-rollback"
    with factory.transaction() as holder:
        holder.execute(
            text("SELECT task_id FROM tasks WHERE task_id=:id FOR UPDATE"),
            {"id": task_id},
        )
        start = time.monotonic()
        with pytest.raises(DBAPIError) as exc:
            with dispatch_budget(time.time() + 0.6):
                with repo.transaction() as db:
                    db.insert_task(
                        (
                            rolled_back_id,
                            "{}",
                            REPO,
                            "branch",
                            BASE,
                            "medium",
                            0,
                            "AWAITING_HANDOFF",
                            1.0,
                            1.0,
                        )
                    )
                    db.find_task((task_id,))
        assert getattr(exc.value.orig, "sqlstate", None) in {"55P03", "57014"}
        assert time.monotonic() - start < 3.0
    with repo.read() as db:
        assert db.find_task((rolled_back_id,)) is None
    with repo.transaction() as db:
        assert db.find_task((task_id,))["current_head_sha"] == HEAD
        db.set_head((NEXT, task_id))
    with repo.read() as db:
        assert db.find_task((task_id,))["current_head_sha"] == NEXT


def test_claim_lock_timeout_is_sanitized_and_retry_succeeds(setup):
    repo, task_id, factory = setup
    ready(repo, task_id)
    before = time.monotonic()
    with factory.transaction() as holder:
        holder.execute(
            text("SELECT task_id FROM tasks WHERE task_id=:id FOR UPDATE"),
            {"id": task_id},
        )
        with dispatch_budget(time.time() + 1.5):
            with pytest.raises(
                PersistenceUnavailableError, match="^PostgreSQL unit of work failed$"
            ) as error:
                claim_task(repo, task_id, HEAD, "lead")
    assert error.value.__suppress_context__
    assert time.monotonic() - before < 6
    with repo.read() as unit:
        assert unit.find_active_claim((task_id,)) is None
        assert unit.find_task((task_id,))["status"] == "READY_IN_REVIEW"
        assert [entry["action"] for entry in unit.list_events((task_id,))][-1:] == [
            "HANDOFF"
        ]
    assert claim_task(repo, task_id, HEAD, "lead")["review_round"] == 1


def test_handoff_lock_timeout_is_sanitized_and_retry_succeeds(setup):
    repo, task_id, factory = setup
    authorize_task(factory, spec(task_id))
    before = time.monotonic()
    with factory.transaction() as conn:
        conn.execute(
            text("SELECT task_id FROM tasks WHERE task_id = :id FOR UPDATE"),
            {"id": task_id},
        )
        with dispatch_budget(time.time() + 1.5):
            with pytest.raises(
                PersistenceUnavailableError, match="PostgreSQL unit of work failed"
            ) as error:
                record_handoff(
                    factory,
                    task_id,
                    REPO,
                    "task/test",
                    BASE,
                    HEAD,
                    "docs/handoff.md",
                    "passed",
                )
    assert error.value.__cause__ is None
    assert time.monotonic() - before < 6
    with repo.read() as unit:
        task = unit.find_task((task_id,))
        assert task["status"] == "AWAITING_HANDOFF"
        assert task["current_head_sha"] is None
        assert len(unit.list_events((task_id,))) == 1
    result = record_handoff(
        factory, task_id, REPO, "task/test", BASE, HEAD, "docs/handoff.md", "passed"
    )
    assert result.head_sha == HEAD


def test_null_handoff_sha_does_not_match(setup):
    repo, task_id, _ = setup
    authorize_task(repo.factory, spec(task_id))
    with repo.transaction() as db:
        db.insert_event(
            (
                task_id,
                "AWAITING_HANDOFF",
                "AWAITING_HANDOFF",
                "HANDOFF",
                None,
                1.0,
                "{}",
            )
        )
        assert db.find_head_handoff(task_id, None) is None
        assert isinstance(db.find_authorization((task_id,))["metadata"], str)


def test_handoff_repository_chooses_latest_matching_event_and_ignores_null(setup):
    repo, task_id, factory = setup
    ready(repo, task_id)
    with repo.transaction() as unit:
        for action, sha, path in (
            ("CI_REPAIR_HANDOFF", HEAD, "latest"),
            ("HANDOFF", NEXT, "different head"),
            ("HANDOFF", None, "null head"),
            ("LOCAL_AUTHORIZATION", HEAD, "different action"),
        ):
            unit.insert_event(
                (
                    task_id,
                    None,
                    "READY_IN_REVIEW",
                    action,
                    sha,
                    0,
                    json.dumps({"handoff_file_path": path}),
                )
            )
    with factory.get_scoped_session() as session:
        handoff = PostgresHandoffRepository(session)
        with SqlAlchemyUnitOfWork(session):
            assert handoff.handoff_path(task_id, None) == (False, None)
            assert handoff.handoff_path(task_id, HEAD) == (True, "latest")
            assert handoff.handoff_path(task_id, NEXT) == (True, "different head")


def test_mcp_review_writes_two_rounds_and_duplicate(setup, mcp_postgres, monkeypatch):
    repo, task_id, factory = setup
    ready(repo, task_id)
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "0")
    claim = mcp_postgres(
        "claim_review", {"task_id": task_id, "expected_head_sha": HEAD}
    )
    first = payload(claim)
    result = mcp_postgres("submit_review", {"submission": first})
    assert result["verdict"] == "CHANGES_REQUESTED" and result["review_round"] == 1
    assert mcp_postgres("submit_review", {"submission": first}) == {
        "task_id": task_id,
        "duplicate": True,
        "reviewed_head_sha": HEAD,
        "verdict": "CHANGES_REQUESTED",
    }
    with pytest.raises(ToolError, match="conflicting duplicate"):
        mcp_postgres("submit_review", {"submission": {**first, "summary": "changed"}})
    record_handoff(
        repo.factory, task_id, REPO, "task/test", BASE, NEXT, "docs/handoff.md", "test"
    )
    claim2 = mcp_postgres(
        "claim_review", {"task_id": task_id, "expected_head_sha": NEXT}
    )
    second = payload(claim2, "APPROVED", prior_resolutions={"F-1": "FIXED"})
    result2 = mcp_postgres("submit_review", {"submission": second})
    assert result2["verdict"] == "APPROVED" and result2["review_round"] == 2
    context = mcp_postgres("get_review_context", {"task_id": task_id})
    result = mcp_postgres("get_review_result", {"task_id": task_id})
    assert context["status"] == result["status"] == "APPROVED"
    assert context["completed_review_rounds"] == 2
    assert [r["round_number"] for r in context["previous_reviews"]] == [1, 2]
    assert context["previous_reviews"][1]["prior_resolutions"] == {"F-1": "FIXED"}
    assert result["latest_reviewed_sha"] == NEXT
    assert result["approval_current"] is None
    assert result["actual_effort"] is None
    assert mcp_postgres("list_review_queue", {}) == {"tasks": []}
    with repo.read() as unit:
        assert len(unit.list_reviews((task_id,))) == 2


def test_submit_requires_claim_owner_exact_head_ci_and_verified_effort(setup):
    repo, task_id, factory = setup
    authorize_task(factory, spec(task_id, verified=True))
    record_handoff(
        factory, task_id, REPO, "task/test", BASE, HEAD, "docs/handoff.md", "test"
    )
    claim = claim_task(repo, task_id, HEAD, "lead")
    submission = payload(claim, "APPROVED")
    with factory.transaction() as connection:
        connection.execute(
            text(
                "INSERT INTO controller_ci_tasks(task_id,pr_number,head_sha,verdict) "
                "VALUES (:id,7,:head,'SUCCESS')"
            ),
            {"id": task_id, "head": NEXT},
        )

    with pytest.raises(WorkflowError, match="claim owner mismatch"):
        submit_task(repo, submission, "other", require_ci=True)
    with pytest.raises(WorkflowError, match="verified exact-HEAD CI"):
        submit_task(repo, submission, "lead", require_ci=True)
    with factory.transaction() as connection:
        connection.execute(
            text("UPDATE controller_ci_tasks SET head_sha=:head WHERE task_id=:id"),
            {"id": task_id, "head": HEAD},
        )
    with pytest.raises(WorkflowError, match="required effort cannot be verified"):
        submit_task(repo, submission, "lead", require_ci=True)
    with repo.read() as unit:
        assert unit.list_reviews((task_id,)) == []
        assert unit.find_active_claim((task_id,))["token"] == claim["claim_token"]
        assert unit.find_task((task_id,))["completed_review_rounds"] == 0

    failure = payload(claim)
    assert submit_task(repo, failure, "lead", require_ci=True)["review_round"] == 1
    with repo.read() as unit:
        committed = unit.find_claim((claim["claim_token"], task_id))
        events = unit.list_events((task_id,))
    assert committed["status"] == "SUBMITTED"
    with factory.transaction() as connection:
        connection.execute(
            text("UPDATE controller_ci_tasks SET head_sha=:head WHERE task_id=:id"),
            {"id": task_id, "head": NEXT},
        )
    assert submit_task(repo, failure, "lead", require_ci=True)["duplicate"]
    with repo.read() as unit:
        assert unit.find_claim((claim["claim_token"], task_id)) == committed
        assert unit.list_events((task_id,)) == events


def test_submit_finding_database_error_rolls_back_review_and_allows_retry(setup):
    repo, task_id, factory = setup
    ready(repo, task_id)
    claim = claim_task(repo, task_id, HEAD, "lead")
    valid = payload(claim)
    oversized_line = {**valid, "findings": [{**valid["findings"][0], "line": 2**31}]}
    with pytest.raises(
        PersistenceUnavailableError, match="^PostgreSQL unit of work failed$"
    ):
        submit_task(repo, oversized_line, "lead")
    with repo.read() as unit:
        assert unit.list_reviews((task_id,)) == []
        assert unit.find_active_claim((task_id,))["token"] == claim["claim_token"]
        assert unit.find_task((task_id,))["completed_review_rounds"] == 0
    with factory.transaction() as connection:
        assert (
            connection.scalar(
                select(ReviewFinding.finding_id).where(ReviewFinding.task_id == task_id)
            )
            is None
        )
    assert submit_task(repo, valid, "lead")["review_round"] == 1


def test_mcp_review_write_failure_rolls_back(setup, mcp_postgres, monkeypatch):
    repo, task_id, _ = setup
    ready(repo, task_id)
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "0")
    claim = mcp_postgres(
        "claim_review", {"task_id": task_id, "expected_head_sha": HEAD}
    )
    original = PostgresReviewWrites.transition

    def fail(self, task, status, action, now, metadata):
        if action == "REVIEW_SUBMITTED":
            raise RuntimeError("injected persistence failure")
        return original(self, task, status, action, now, metadata)

    monkeypatch.setattr(PostgresReviewWrites, "transition", fail)
    with pytest.raises(ToolError, match="injected persistence failure"):
        mcp_postgres("submit_review", {"submission": payload(claim)})
    with repo.read() as unit:
        assert unit.list_reviews((task_id,)) == []
        assert unit.find_claim((claim["claim_token"], task_id))["status"] == "ACTIVE"
        assert unit.find_task((task_id,))["completed_review_rounds"] == 0
        assert unit.find_task((task_id,))["status"] == "IN_REVIEW"


@pytest.mark.parametrize("operation", ["claim", "submit"])
@pytest.mark.parametrize(
    "checkpoint", ["before-session-commit", "after-session-commit"]
)
def test_mcp_review_deadline_failure_rolls_back(
    setup, mcp_postgres, monkeypatch, operation, checkpoint
):
    repo, task_id, _ = setup
    ready(repo, task_id)
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "0")
    claim_arguments = {"task_id": task_id, "expected_head_sha": HEAD}
    if operation == "submit":
        claim = mcp_postgres("claim_review", claim_arguments)
        arguments = {"submission": payload(claim)}
    else:
        arguments = claim_arguments

    with repo.read() as unit:
        original_task = unit.find_task((task_id,))
        original_events = unit.list_events((task_id,))
        original_claim = unit.find_active_claim((task_id,))
    clock = SimpleNamespace(now=1000.0)
    fake_time = SimpleNamespace(time=lambda: clock.now, monotonic=lambda: clock.now)
    original_write = getattr(PostgresReviewWrites, operation)
    session_commits = []

    def after_session_commit(_session):
        session_commits.append(True)
        if checkpoint == "after-session-commit":
            clock.now = 1002.0

    def write_and_expire(self, *args):
        original_write(self, *args)
        event.listen(self._session, "after_commit", after_session_commit)
        if checkpoint == "before-session-commit":
            clock.now = 1002.0

    with monkeypatch.context() as patch:
        patch.setattr(budget, "time", fake_time)
        patch.setattr(PostgresReviewWrites, operation, write_and_expire)
        with dispatch_budget(1001.0):
            with pytest.raises(ToolError, match="dispatch deadline exceeded"):
                mcp_postgres(f"{operation}_review", arguments)

    assert session_commits == ([True] if checkpoint == "after-session-commit" else [])
    with repo.read() as unit:
        assert unit.list_reviews((task_id,)) == []
        assert unit.find_task((task_id,)) == original_task
        assert unit.list_events((task_id,)) == original_events
        assert unit.find_active_claim((task_id,)) == original_claim
    result = mcp_postgres(f"{operation}_review", arguments)
    assert result["review_round"] == 1
