"""V1-03C coordinator: signed hint never substitutes GitHub trusted checks."""
import json
from pathlib import Path

from agent_workflow.domain.service import WorkflowService
from agent_workflow.integrations.ci_controller import ControllerCiCoordinator
from agent_workflow.storage.ci_state import ControllerCiState
from agent_workflow.storage.webhook_inbox import WebhookInbox, WebhookNotification

REPO = "builacviet/agent-workflow"
SHA = "b" * 40


def fixture(tmp_path):
    db = tmp_path / "state.db"
    service = WorkflowService(db)
    service.provision_task(dict(
        task_id="T", title="Test", description="Test", acceptance_criteria=["AC"],
        definition_of_done="test", repository=REPO, branch="codex/test",
        base_sha="a"*40, requested_effort="high", risk_level="high",
        require_verified_effort=True, context_paths=["docs/handoff.md"], synthetic=True,
    ))
    service.register_task_handoff("T", REPO, "codex/test", "a"*40, SHA, "docs/handoff.md", "test")
    state = ControllerCiState(db)
    state.track("T", 31, SHA)
    notification = WebhookNotification(
        "12345678-1234-1234-1234-123456789abc",
        "workflow_run", "completed", REPO, 17, 321, "c" * 40,
    )
    WebhookInbox(db).record(notification, "1" * 64)
    return db, state


class FakeChecks:
    installation_id = 17
    def __init__(self, *, unavailable=False, head=SHA):
        self.unavailable = unavailable
        self.head = head
        self.calls = 0

    def get_pull_request(self, repo, number):
        self.calls += 1
        if self.unavailable:
            raise OSError("offline")
        return {"number": number, "base": {"repo": {"full_name": repo}},
                "head": {"sha": self.head}}

    def list_check_runs(self, repo, sha):
        self.calls += 1
        return {"total_count": 1, "check_runs": [{
            "id": 3, "name": "Python tests", "head_sha": sha,
            "app": {"slug": "github-actions"}, "status": "completed",
            "conclusion": "success", "completed_at": "2026-11-10T00:00:00Z",
            "event": "push", "workflow_path": ".github/workflows/v1-ci.yml",
            "tested_sha": sha, "workflow_run_id": 44, "run_attempt": 1,
        }]}


def test_webhook_sha_is_ignored_and_current_pr_head_is_reverified(tmp_path):
    db, state = fixture(tmp_path)
    checks = FakeChecks()
    ci = ControllerCiCoordinator(state, checks, installation_id=17)
    observed = ci.reconcile_once()
    assert len(observed) == 1 and observed[0].verdict == "SUCCESS"
    assert state.approved("T", SHA)
    assert state.pending_events() == []
    before = checks.calls
    assert ci.reconcile_once() == ()
    assert checks.calls == before
    assert ControllerCiState(db).approved("T", SHA)


def test_unavailable_github_keeps_inbox_for_restart_replay(tmp_path):
    db, state = fixture(tmp_path)
    ci = ControllerCiCoordinator(state, FakeChecks(unavailable=True), installation_id=17)
    observed = ci.reconcile_once()
    assert observed[0].verdict == "BLOCKED"
    assert len(state.pending_events()) == 1
    recovered = ControllerCiCoordinator(
        ControllerCiState(db), FakeChecks(), installation_id=17,
    )
    assert recovered.reconcile_once()[0].verdict == "SUCCESS"
    assert recovered.state.pending_events() == []


def test_stale_current_head_fails_closed(tmp_path):
    _, state = fixture(tmp_path)
    ci = ControllerCiCoordinator(state, FakeChecks(head="d"*40), installation_id=17)
    observed = ci.reconcile_once()
    assert (observed[0].verdict, observed[0].reason) == ("BLOCKED", "PR_HEAD_CHANGED")
    assert not state.approved("T", SHA)


@pytest.mark.parametrize(
    ("first_status", "expected_reason"),
    [
        ("missing", "REQUIRED_CHECK_MISSING"),
        ("pending", "CHECK_PENDING"),
    ],
)
def test_nonterminal_checks_remain_retryable_without_new_webhook(
    tmp_path, first_status, expected_reason,
):
    """A webhook must not be settled before the required check is observable.

    GitHub's check indexing can lag the completed workflow notification.
    The following reconciliation has no second webhook or process restart.
    """
    db, state = fixture(tmp_path)

    class EventuallyVisibleChecks(FakeChecks):
        phase = first_status

        def list_check_runs(self, repo, sha):
            response = super().list_check_runs(repo, sha)
            if self.phase == "missing":
                return {"total_count": 0, "check_runs": []}
            if self.phase == "pending":
                check = response["check_runs"][0]
                check["status"] = "in_progress"
                check["conclusion"] = None
                check["completed_at"] = None
            return response

    checks = EventuallyVisibleChecks()
    coordinator = ControllerCiCoordinator(state, checks, installation_id=17)

    first = coordinator.reconcile_once()
    assert len(first) == 1
    assert (first[0].verdict, first[0].reason) == ("BLOCKED", expected_reason)
    assert not state.approved("T", SHA)
    # A nonterminal observation must retain the already signed wake-up event.
    assert len(ControllerCiState(db).pending_events()) == 1

    checks.phase = "green"
    recovered = coordinator.reconcile_once()
    assert len(recovered) == 1
    assert (recovered[0].verdict, recovered[0].reason) == (
        "SUCCESS", "REQUIRED_CHECK_PASSED",
    )
    assert ControllerCiState(db).approved("T", SHA)
    assert coordinator.state.pending_events() == []
    assert coordinator.reconcile_once() == ()


def test_terminal_ci_failure_settles_notification_without_polling(tmp_path):
    """Do not keep polling completed failures when no repair HEAD is authorized."""
    _, state = fixture(tmp_path)

    class FailedChecks(FakeChecks):
        def list_check_runs(self, repo, sha):
            payload = super().list_check_runs(repo, sha)
            payload["check_runs"][0]["conclusion"] = "failure"
            return payload

    coordinator = ControllerCiCoordinator(state, FailedChecks(), installation_id=17)
    observed = coordinator.reconcile_once()
    assert len(observed) == 1
    assert (observed[0].verdict, observed[0].reason) == (
        "FAILURE", "REQUIRED_CHECK_FAILED",
    )
    assert state.pending_events() == []
    assert not state.approved("T", SHA)
