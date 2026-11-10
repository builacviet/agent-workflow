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
