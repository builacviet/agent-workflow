"""Live PostgreSQL workflow contract checks on a disposable database."""

import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from test_my_wfc import BASE, HEAD, NEXT, REPO, payload, spec

from agent_workflow.app_config import AppConfig
from agent_workflow.budget import dispatch_budget
from agent_workflow.domain.service import WorkflowService
from agent_workflow.integrations.github import WorkflowError
from agent_workflow.storage.postgres_connection import PostgresFactory
from agent_workflow.storage.postgres_workflow import PostgresWorkflowRepository


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


def test_service_history_and_replay(setup):
    service, _, task_id, _ = setup
    ready(service, task_id)
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


def test_ci_repair_requires_matching_failure_evidence(setup):
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
