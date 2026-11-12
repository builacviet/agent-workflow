"""Durable CI state: exact SHA, repair count and crash-after-inbox recovery."""
import pytest

from agent_workflow.domain.service import WorkflowService
from agent_workflow.domain.states import CiVerdict
from agent_workflow.integrations.ci_gate import CiEvidence
from agent_workflow.integrations.github import WorkflowError
from agent_workflow.storage.ci_state import ControllerCiState
from agent_workflow.storage.webhook_inbox import WebhookInbox, WebhookNotification

A, B, C, D, E = (letter * 40 for letter in "abcde")
REPO = "builacviet/agent-workflow"


def setup(tmp_path):
    path = tmp_path / "state.db"
    service = WorkflowService(path)
    service.provision_task(dict(
        task_id="T", title="Test", description="Test", acceptance_criteria=["AC1"],
        definition_of_done="test", repository=REPO, branch="codex/test",
        base_sha=A, requested_effort="high", risk_level="high",
        require_verified_effort=True, context_paths=["docs/handoff.md"], synthetic=True,
    ))
    service.register_task_handoff("T", REPO, "codex/test", A, B, "docs/handoff.md", "test")
    return service, ControllerCiState(path)


def evidence(verdict="SUCCESS", sha=B):
    return CiEvidence(
        "T", REPO, 17, 21, sha, sha, sha, "Python tests",
        "github-actions", verdict, "REQUIRED_CHECK_PASSED" if verdict == "SUCCESS"
        else "REQUIRED_CHECK_FAILED", 7, 44, 1,
        "2026-11-10T00:00:00Z", "2026-11-10T00:01:00Z",
    )


def test_exact_identity_and_durable_success(tmp_path):
    service, state = setup(tmp_path)
    state.track("T", 21, B)
    assert not state.approved("T", B)
    state.record(evidence())
    assert state.approved("T", B)
    assert ControllerCiState(service.db_path).approved("T", B)
    assert not state.approved("T", A)
    with pytest.raises(WorkflowError):
        state.track("T", 22, B)


def test_ci_failure_repair_head_limit(tmp_path):
    service, state = setup(tmp_path)
    state.track("T", 21, B)
    state.record(evidence(CiVerdict.FAILURE))
    state.record(evidence(CiVerdict.BLOCKED))
    assert state.tracked()[0]["verdict"] == CiVerdict.FAILURE
    assert state.previous_failure("T", B)
    for n, sha in enumerate((C, D), start=1):
        service.register_task_handoff(
            "T", REPO, "codex/test", A, sha, "docs/handoff.md", "repair"
        )
        state.track("T", 21, sha)
        assert state.tracked()[0]["repair_heads"] == n
        state.record(evidence(CiVerdict.FAILURE, sha))
    with pytest.raises(WorkflowError, match="repair"):
        service.register_task_handoff(
            "T", REPO, "codex/test", A, E, "docs/handoff.md", "repair"
        )
    assert state.tracked()[0]["head_sha"] == D
    assert state.tracked()[0]["repair_heads"] == 2


def test_track_cannot_repair_an_unaudited_head_change(tmp_path):
    service, state = setup(tmp_path)
    state.track("T", 21, B)
    state.record(evidence(CiVerdict.FAILURE))
    events = service.get_review_context("T")["events"]
    with service.repository.raw_transaction() as db:
        db.execute("UPDATE tasks SET current_head_sha=? WHERE task_id='T'", (C,))

    with pytest.raises(WorkflowError, match="HEAD differs from persisted binding"):
        state.track("T", 21, C)

    with service.repository.raw_connection() as db:
        row = db.execute(
            "SELECT pr_number, head_sha, verdict, repair_heads "
            "FROM controller_ci_tasks WHERE task_id='T'"
        ).fetchone()
    assert tuple(row) == (21, B, CiVerdict.FAILURE, 0)
    assert service.get_review_context("T")["events"] == events


def test_webhook_unprocessed_recovered_across_restart(tmp_path):
    service, state = setup(tmp_path)
    notification = WebhookNotification(
        "12345678-1234-1234-1234-123456789abc",
        "pull_request", "synchronize", REPO, 17, 21, B,
    )
    WebhookInbox(service.db_path).record(notification, "a" * 64)
    restarted = ControllerCiState(service.db_path)
    assert [e.delivery_id for e in restarted.pending_events()] == [notification.delivery_id]
    restarted.mark_processed(notification.delivery_id)
    restarted.mark_processed(notification.delivery_id)
    assert ControllerCiState(service.db_path).pending_events() == []
