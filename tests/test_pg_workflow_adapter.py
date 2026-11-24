"""Live PostgreSQL workflow contract checks on a disposable database."""

import asyncio
import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.fastmcp.exceptions import ToolError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from test_agent_workflow import BASE, HEAD, NEXT, REPO, payload, spec

from agent_workflow.app_config import AppConfig
from agent_workflow.budget import dispatch_budget
from agent_workflow.composition.postgres_mcp import postgres_mcp_service
from agent_workflow.domain.service import WorkflowService
from agent_workflow.infrastructure.database.readers.review_queue_reader import (
    PostgresReviewQueueReader,
)
from agent_workflow.integrations.github import WorkflowError
from agent_workflow.mcp.server import mcp
from agent_workflow.storage.postgres_connection import PostgresFactory
from agent_workflow.storage.postgres_workflow import PostgresWorkflowRepository


@pytest.mark.parametrize("require_ci", [True, False])
def test_mcp_composition_owns_factory_and_config(require_ci):
    config = AppConfig(
        pg_runtime_dsn="postgresql+psycopg://user:password@localhost/test",
        ci_gate_required=require_ci,
    )
    factory = Mock()
    with patch.object(PostgresFactory, "open", return_value=factory) as open_factory:
        with postgres_mcp_service(config) as service:
            open_factory.assert_called_once_with(config)
            assert isinstance(service.repository, PostgresWorkflowRepository)
            assert service.repository.factory is factory
            assert service.require_ci is require_ci
            factory.close.assert_not_called()
        factory.close.assert_called_once_with()


def test_mcp_composition_closes_on_scope_and_construction_errors():
    config = AppConfig(
        pg_runtime_dsn="postgresql+psycopg://user:password@localhost/test"
    )
    factory = Mock()
    with patch.object(PostgresFactory, "open", return_value=factory):
        with pytest.raises(RuntimeError, match="scope error"):
            with postgres_mcp_service(config):
                raise RuntimeError("scope error")
        factory.close.assert_called_once_with()
        factory.reset_mock()
        with patch(
            "agent_workflow.composition.postgres_mcp.WorkflowService",
            side_effect=RuntimeError("construction error"),
        ):
            with pytest.raises(RuntimeError, match="construction error"):
                with postgres_mcp_service(config):
                    pytest.fail("service must not be yielded")
        factory.close.assert_called_once_with()


@pytest.mark.parametrize("dsn", [None, "sqlite:///fallback.db", "invalid"])
def test_mcp_composition_rejects_missing_or_invalid_dsn(dsn):
    config = AppConfig.model_construct(pg_runtime_dsn=dsn)
    with patch("agent_workflow.storage.postgres_connection.create_engine") as engine:
        with patch("agent_workflow.domain.service.SQLiteWorkflowRepository") as sqlite:
            with pytest.raises(ValueError, match="PG_RUNTIME_DSN"):
                with postgres_mcp_service(config):
                    pytest.fail("service must not be yielded")
            engine.assert_not_called()
            sqlite.assert_not_called()


def test_factory_uow_deadline_hook_can_rollback_after_session_commit():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num text)"))
        connection.execute(
            text("INSERT INTO alembic_version VALUES ('pg02d_webhook_digest')")
        )
        connection.execute(text("CREATE TABLE uow_items (value integer)"))

    def expired():
        raise TimeoutError("deadline expired")

    with PostgresFactory(engine) as factory:
        with factory.unit_of_work_session() as session:
            session.execute(text("INSERT INTO uow_items VALUES (1)"))
        with pytest.raises(TimeoutError, match="deadline expired"):
            with factory.unit_of_work_session(after_session_commit=expired) as session:
                session.execute(text("INSERT INTO uow_items VALUES (2)"))
        with factory.transaction() as connection:
            assert connection.execute(
                text("SELECT value FROM uow_items")
            ).scalars().all() == [1]


@pytest.fixture
def setup():
    dsn = os.environ.get("PG_TEST_DSN")
    if not dsn:
        pytest.skip("disposable PG_TEST_DSN not supplied")
    task_id = "PG02B-" + uuid.uuid4().hex
    with PostgresFactory.open(AppConfig(pg_runtime_dsn=dsn)) as factory:
        repo = PostgresWorkflowRepository(factory)
        service = WorkflowService(
            repository=repo, require_ci=False, clock=lambda: 1000.0
        )
        try:
            yield service, repo, task_id, factory
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


def ready(service, task_id):
    service.provision_task(spec(task_id))
    service.register_task_handoff(
        task_id, REPO, "task/test", BASE, HEAD, "docs/handoff.md", "test"
    )


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
    service, _, task_id, _ = setup
    service.provision_task(spec(task_id))
    assert service.get_review_context(task_id)["current_head_sha"] is None
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
    assert context == service.get_review_context(task_id)


@pytest.mark.parametrize("verdict", ["APPROVED", "ESCALATED"])
def test_mcp_context_and_result_match_persisted_history(setup, mcp_postgres, verdict):
    service, _, task_id, _ = setup
    arguments = {"task_id": task_id}
    service.provision_task(
        {**spec(task_id), "context_paths": ["docs/review context.md"]}
    )

    def assert_parity():
        assert mcp_postgres(
            "get_review_context", arguments
        ) == service.get_review_context(task_id)
        assert mcp_postgres(
            "get_review_result", arguments
        ) == service.get_review_result(task_id)

    assert_parity()
    for sha, outcome, resolutions in (
        (HEAD, "CHANGES_REQUESTED", {}),
        (NEXT, verdict, {"F-1": "FIXED" if verdict == "APPROVED" else "NOT_FIXED"}),
    ):
        service.register_task_handoff(
            task_id, REPO, "task/test", BASE, sha, "docs/review context.md", "test"
        )
        claim = service.claim_review(task_id, sha, "lead")
        service.submit_review(
            payload(
                claim,
                outcome,
                prior_resolutions=resolutions,
                **({"findings": []} if sha == NEXT else {}),
            ),
            "lead",
        )
        assert_parity()

    for name in ("get_review_context", "get_review_result"):
        with pytest.raises(ToolError, match="unknown authorized task"):
            mcp_postgres(name, {"task_id": f"{task_id}-missing"})


def test_mcp_queue_filters_and_recovers_a_lease_in_the_workflow_transaction(
    setup,
    mcp_postgres,
):
    service, repo, task_id, factory = setup
    ready(service, task_id)
    claim = service.claim_review(task_id, HEAD, "lead")
    assert service.get_review_context(task_id)["status"] == "IN_REVIEW"

    # Even an effort filter with no matches recovers stale leases.
    assert mcp_postgres("list_review_queue", {"requested_effort": "invalid"}) == {
        "tasks": []
    }
    with repo.read() as unit:
        assert unit.find_claim((claim["claim_token"], task_id))["status"] == "EXPIRED"
    assert (
        service.get_review_context(task_id)["events"][-1]["action"] == "LEASE_EXPIRED"
    )

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
    expected = service.list_review_queue("medium")
    assert [task["task_id"] for task in expected] == [task_id]
    assert mcp_postgres("list_review_queue", {"requested_effort": "medium"}) == {
        "tasks": expected
    }


def test_mcp_queue_read_failure_rolls_back_lease_recovery(
    setup, mcp_postgres, monkeypatch
):
    service, repo, task_id, _ = setup
    ready(service, task_id)
    claim = service.claim_review(task_id, HEAD, "lead")

    def fail_read(self, requested_effort):
        raise RuntimeError("synthetic queue read failure")

    monkeypatch.setattr(PostgresReviewQueueReader, "read", fail_read)
    with pytest.raises(ToolError, match="synthetic queue read failure"):
        mcp_postgres("list_review_queue", {})

    with repo.read() as unit:
        assert unit.find_claim((claim["claim_token"], task_id))["status"] == "ACTIVE"
    context = service.get_review_context(task_id)
    assert context["status"] == "IN_REVIEW"
    assert context["events"][-1]["action"] == "CLAIM"


def test_service_history_and_replay(setup):
    _, _, task_id, _ = setup
    config = AppConfig(pg_runtime_dsn=os.environ["PG_TEST_DSN"], ci_gate_required=False)
    with postgres_mcp_service(config) as service:
        ready(service, task_id)
    with postgres_mcp_service(config) as service:
        assert service.get_review_context(task_id)["current_head_sha"] == HEAD
    # The fixture owns a separate factory for the remaining workflow and cleanup.
    service, _, _, _ = setup
    with pytest.raises(WorkflowError, match="stale"):
        service.claim_review(task_id, NEXT, "lead")
    claim = service.claim_review(task_id, HEAD, "lead")
    with pytest.raises(WorkflowError, match="stale"):
        service.submit_review(payload(claim, reviewed_head_sha=NEXT), "lead")
    result = service.submit_review(payload(claim), "lead")
    assert service.submit_review(payload(claim), "lead")["duplicate"]
    with pytest.raises(WorkflowError, match="conflicting"):
        service.submit_review(payload(claim, summary="different"), "lead")
    context = service.get_review_context(task_id)
    assert context["current_head_sha"] == HEAD
    assert context["previous_reviews"][0]["review_id"] == result["review_id"]
    assert context["previous_reviews"][0]["findings"][0]["finding_id"] == "F-1"
    assert [e["action"] for e in context["events"]] == [
        "LOCAL_AUTHORIZATION",
        "HANDOFF",
        "CLAIM",
        "REVIEW_SUBMITTED",
    ]
    assert context["events"][-1]["sha"] == HEAD


def test_rollback_and_constraints(setup):
    service, repo, task_id, _ = setup
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
    ready(service, task_id)
    with pytest.raises(IntegrityError):
        with repo.transaction() as db:
            db.set_head((NEXT, task_id))
            db.insert_claim(("one", task_id, HEAD, "lead", 1.0, 2.0))
            db.insert_claim(("two", task_id, HEAD, "lead", 1.0, 2.0))
    with repo.read() as db:
        assert db.find_task((task_id,))["current_head_sha"] == HEAD
        assert db.find_active_claim((task_id,)) is None


def test_read_keeps_repeatable_read_snapshot_and_rejects_writes(setup):
    service, repo, task_id, factory = setup
    ready(service, task_id)
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
    _, repo, task_id, factory = setup
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
    service, repo, task_id, _ = setup
    ready(service, task_id)
    guarded = WorkflowService(repository=repo, require_ci=True, clock=lambda: 1000.0)
    with pytest.raises(WorkflowError, match="CI"):
        guarded.claim_review(task_id, HEAD, "lead")

    def claim():
        try:
            return service.claim_review(task_id, HEAD, "lead")
        except WorkflowError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: claim(), range(2)))
    assert sum(c is not None for c in claims) == 1
    with repo.read() as db:
        assert db.find_active_claim((task_id,)) is not None
        assert not db.ci_approved((task_id, NEXT))


def test_history_helpers_reject(setup):
    _, repo, _, _ = setup
    with repo.transaction() as db:
        with pytest.raises(WorkflowError):
            db.import_historical_head((HEAD, 1))
        with pytest.raises(WorkflowError):
            db.delete_synthetic_history(("tasks", "POC-REVIEW-001"))


def test_ci_repair_requires_matching_failure_evidence(setup, mcp_postgres, monkeypatch):
    service, repo, task_id, factory = setup
    ready(service, task_id)
    with factory.transaction() as conn:
        conn.execute(
            text(
                "INSERT INTO controller_ci_tasks(task_id,pr_number,head_sha,verdict) VALUES (:id,7,:head,'FAILURE')"
            ),
            {"id": task_id, "head": HEAD},
        )
    with pytest.raises(WorkflowError, match="not authorized"):
        service.register_task_handoff(
            task_id, REPO, "task/test", BASE, NEXT, "docs/handoff.md", "repair"
        )
    with factory.transaction() as conn:
        conn.execute(
            text(
                "INSERT INTO controller_ci_observations(task_id,pr_number,head_sha,verdict,reason,check_run_id,workflow_run_id,run_attempt,observed_at) VALUES (:id,7,:head,'FAILURE','REQUIRED_CHECK_FAILED',1,2,1,'now')"
            ),
            {"id": task_id, "head": HEAD},
        )
    service.register_task_handoff(
        task_id, REPO, "task/test", BASE, NEXT, "docs/handoff.md", "repair"
    )
    with repo.read() as db:
        assert not db.ci_approved((task_id, NEXT))
        assert db.find_head_handoff(task_id, NEXT)["action"] == "CI_REPAIR_HANDOFF"
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "0")
    assert mcp_postgres("list_review_queue", {}) == {"tasks": []}
    with pytest.raises(WorkflowError, match="CI"):
        service.claim_review(task_id, NEXT, "lead")
    with factory.transaction() as conn:
        conn.execute(
            text("UPDATE controller_ci_tasks SET verdict='SUCCESS' WHERE task_id=:id"),
            {"id": task_id},
        )
    with repo.read() as db:
        assert db.ci_approved((task_id, NEXT))
        assert not db.ci_approved((task_id, HEAD))
    assert mcp_postgres("list_review_queue", {}) == {
        "tasks": service.list_review_queue()
    }
    assert service.claim_review(task_id, NEXT, "lead")["reviewed_sha"] == NEXT


def test_expired_claim_recovery(setup):
    _, repo, task_id, _ = setup
    now = [1000.0]
    service = WorkflowService(
        repository=repo, require_ci=False, clock=lambda: now[0], lease_seconds=10
    )
    ready(service, task_id)
    first = service.claim_review(task_id, HEAD, "lead")
    now[0] += 11
    second = service.claim_review(task_id, HEAD, "lead")
    with pytest.raises(WorkflowError, match="expired"):
        service.submit_review(payload(first), "lead")
    assert second["claim_token"] != first["claim_token"]
    assert second["review_round"] == 1


def test_concurrent_identical_authorization_is_idempotent(setup):
    service, _, task_id, _ = setup
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: service.provision_task(spec(task_id)), range(2)))
    assert len(service.get_review_context(task_id)["events"]) == 1


def test_second_round_history_and_order(setup):
    service, _, task_id, _ = setup
    ready(service, task_id)
    first = service.claim_review(task_id, HEAD, "lead")
    service.submit_review(payload(first), "lead")
    service.register_task_handoff(
        task_id, REPO, "task/test", BASE, NEXT, "docs/handoff.md", "test"
    )
    second = service.claim_review(task_id, NEXT, "lead")
    data = payload(second, "APPROVED", prior_resolutions={"F-1": "FIXED"})
    service.submit_review(data, "lead")
    result = service.get_review_context(task_id)
    assert [r["round_number"] for r in result["previous_reviews"]] == [1, 2]
    assert result["previous_reviews"][1]["prior_resolutions"] == {"F-1": "FIXED"}
    assert result["previous_reviews"][1]["actual_effort"] is None
    assert result["status"] == "APPROVED"


def test_service_result_matches_sqlite(setup, tmp_path, monkeypatch):
    pg, _, task_id, _ = setup
    sqlite = WorkflowService(
        tmp_path / "workflow.sqlite", require_ci=False, clock=lambda: 1000.0
    )
    monkeypatch.setattr(
        "agent_workflow.domain.service.uuid.uuid4",
        lambda: SimpleNamespace(hex="fixed-id"),
    )
    for service in (sqlite, pg):
        ready(service, task_id)
        claim = service.claim_review(task_id, HEAD, "lead")
        service.submit_review(payload(claim), "lead")
    left, right = sqlite.get_review_context(task_id), pg.get_review_context(task_id)
    # PostgreSQL identity sequences are database-wide; SQLite IDs are file-local.
    for context in (left, right):
        for event in context["events"]:
            event.pop("event_id")
    assert left == right
    assert (
        type(left["require_verified_effort"])
        is type(right["require_verified_effort"])
        is int
    )
    assert right["previous_reviews"][0]["actual_effort"] is None


def test_adversarial_text_is_stored_as_data(setup):
    service, repo, task_id, _ = setup
    ready(service, task_id)
    attack = "lead' OR 1=1 --"
    claim = service.claim_review(task_id, HEAD, attack)
    service.submit_review(payload(claim, summary=attack), attack)
    context = service.get_review_context(task_id)
    assert context["previous_reviews"][0]["summary"] == attack
    with repo.read() as db:
        assert db.find_claim(("' OR 1=1 --", task_id)) is None
        assert len(db.list_reviews((task_id,))) == 1


def test_row_lock_timeout_rolls_back_and_retry_succeeds(setup):
    service, repo, task_id, factory = setup
    ready(service, task_id)
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


def test_null_handoff_sha_does_not_match(setup):
    service, repo, task_id, _ = setup
    service.provision_task(spec(task_id))
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


def test_mcp_review_writes_two_rounds_and_duplicate(setup, mcp_postgres, monkeypatch):
    service, repo, task_id, factory = setup
    ready(service, task_id)
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "0")
    monkeypatch.setattr(
        WorkflowService,
        "claim_review",
        lambda *args: pytest.fail("legacy claim called"),
    )
    monkeypatch.setattr(
        WorkflowService,
        "submit_review",
        lambda *args: pytest.fail("legacy submit called"),
    )
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
    service.register_task_handoff(
        task_id, REPO, "task/test", BASE, NEXT, "docs/handoff.md", "test"
    )
    claim2 = mcp_postgres(
        "claim_review", {"task_id": task_id, "expected_head_sha": NEXT}
    )
    second = payload(claim2, "APPROVED", prior_resolutions={"F-1": "FIXED"})
    result2 = mcp_postgres("submit_review", {"submission": second})
    assert result2["verdict"] == "APPROVED" and result2["review_round"] == 2
    assert mcp_postgres(
        "get_review_context", {"task_id": task_id}
    ) == service.get_review_context(task_id)
    assert mcp_postgres(
        "get_review_result", {"task_id": task_id}
    ) == service.get_review_result(task_id)
    assert mcp_postgres("list_review_queue", {}) == {"tasks": []}
    with repo.read() as unit:
        assert len(unit.list_reviews((task_id,))) == 2


def test_mcp_review_write_failure_rolls_back(setup, mcp_postgres, monkeypatch):
    from agent_workflow.infrastructure.database.repositories.review_writes import (
        PostgresReviewWrites,
    )

    service, repo, task_id, _ = setup
    ready(service, task_id)
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
