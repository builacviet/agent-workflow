"""Reconcile CI inbound events without trusting webhook verdicts."""

from unittest.mock import Mock

import pytest

from agent_workflow.application.ci.use_cases.reconcile_ci import ReconcileCiUseCase
from agent_workflow.domain.ci_evidence import WebhookNotification
from agent_workflow.domain.workflow_identity import WorkflowError

REPO = "builacviet/agent-workflow"
HEAD = "a" * 40


def test_invalid_signed_notification_rejects_without_acking():
    state = Mock()
    state.pending_events.return_value = [
        WebhookNotification(
            "12345678-1234-1234-1234-123456789abc",
            "workflow_run", "completed", "other/repo", 17, 44, HEAD,
        )
    ]
    checks = Mock(installation_id=17)
    with pytest.raises(WorkflowError, match="incompatible notification"):
        ReconcileCiUseCase(state, checks, installation_id=17).execute()
    state.record.assert_not_called()
    state.mark_processed.assert_not_called()


def test_no_pending_event_does_not_poll_checks():
    state = Mock()
    state.pending_events.return_value = []
    checks = Mock(installation_id=17)
    assert ReconcileCiUseCase(state, checks, installation_id=17).execute() == ()
    checks.get_pull_request.assert_not_called()
    checks.list_check_runs.assert_not_called()


def test_github_unavailable_records_blocked_and_retains_pending_event():
    event = WebhookNotification(
        "12345678-1234-1234-1234-123456789abc",
        "workflow_run", "completed", REPO, 17, 44, HEAD,
    )
    state = Mock()
    state.pending_events.return_value = [event]
    state.tracked.return_value = [
        {"task_id": "task-1", "pr_number": 21, "head_sha": HEAD}
    ]
    state.previous_failure.return_value = False
    checks = Mock(installation_id=17)
    checks.get_pull_request.side_effect = OSError("offline")

    result = ReconcileCiUseCase(state, checks, installation_id=17).execute()
    assert len(result) == 1 and result[0].verdict == "BLOCKED"
    assert result[0].reason == "GITHUB_READ_UNAVAILABLE"
    state.record.assert_called_once_with(result[0])
    state.mark_processed.assert_not_called()
