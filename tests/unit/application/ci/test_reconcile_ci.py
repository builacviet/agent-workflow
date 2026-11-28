"""Reconcile CI inbound events without trusting webhook verdicts."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from agent_workflow.application.ci.dtos import CiTrackedTaskSnapshot
from agent_workflow.application.ci.use_cases import reconcile_ci
from agent_workflow.application.ci.use_cases.reconcile_ci import ReconcileCiUseCase
from agent_workflow.application.exceptions import InvalidApplicationRequestError
from agent_workflow.domain.ci_evidence import WebhookNotification

REPO = "builacviet/agent-workflow"
HEAD = "a" * 40
DELIVERY = "12345678-1234-1234-1234-123456789abc"


@pytest.fixture
def reconciliation():
    reader = Mock()
    reader.pending_events.return_value = [
        WebhookNotification(DELIVERY, "workflow_run", "completed", REPO, 17, 44, HEAD)
    ]
    reader.tracked.return_value = (
        CiTrackedTaskSnapshot("task-1", 21, HEAD, "BLOCKED", 0),
    )
    reader.previous_failure.return_value = False
    checks = Mock(installation_id=17)
    recorder, acknowledger, leadership = Mock(), Mock(), Mock()
    scope_events = []

    @contextmanager
    def acquired_scope():
        scope_events.append("enter")
        try:
            yield
        finally:
            scope_events.append("exit")

    leadership.acquired_scope.side_effect = acquired_scope
    effects = Mock()
    effects.attach_mock(leadership.require_owner, "require_owner")
    effects.attach_mock(recorder.execute, "record")
    effects.attach_mock(acknowledger.execute, "acknowledge")
    use_case = ReconcileCiUseCase(
        reader,
        checks,
        recorder=recorder,
        acknowledger=acknowledger,
        leadership=leadership,
        installation_id=17,
    )
    return SimpleNamespace(
        reader=reader,
        checks=checks,
        recorder=recorder,
        acknowledger=acknowledger,
        effects=effects,
        use_case=use_case,
        leadership=leadership,
        scope_events=scope_events,
    )


def test_invalid_signed_notification_rejects_without_acking(reconciliation):
    reconciliation.reader.pending_events.return_value = [
        WebhookNotification(
            "12345678-1234-1234-1234-123456789abc",
            "workflow_run",
            "completed",
            "other/repo",
            17,
            44,
            HEAD,
        )
    ]
    with pytest.raises(
        InvalidApplicationRequestError, match="incompatible notification"
    ):
        reconciliation.use_case.execute()
    assert reconciliation.effects.mock_calls == []
    assert reconciliation.scope_events == ["enter", "exit"]


@pytest.mark.parametrize("operation", ["record", "acknowledge"])
def test_write_exception_propagates_and_releases_pass_scope(
    monkeypatch, reconciliation, operation
):
    evidence = Mock(verdict="SUCCESS")
    monkeypatch.setattr(reconcile_ci, "verify_ci", Mock(return_value=evidence))
    target = (
        reconciliation.recorder.execute
        if operation == "record"
        else reconciliation.acknowledger.execute
    )
    target.side_effect = RuntimeError(f"{operation} failed")

    with pytest.raises(RuntimeError, match=f"{operation} failed"):
        reconciliation.use_case.execute()

    target.assert_called_once()
    assert reconciliation.scope_events == ["enter", "exit"]


def test_each_execute_call_acquires_a_fresh_pass_scope(reconciliation):
    reconciliation.reader.pending_events.return_value = []
    reconciliation.reader.tracked.return_value = ()

    assert reconciliation.use_case.execute() == ()
    assert reconciliation.use_case.execute(startup=True) == ()

    assert reconciliation.scope_events == ["enter", "exit", "enter", "exit"]


def test_no_pending_event_does_not_poll_checks(reconciliation):
    reconciliation.reader.pending_events.return_value = []
    assert reconciliation.use_case.execute() == ()
    reconciliation.checks.get_pull_request.assert_not_called()
    reconciliation.checks.list_check_runs.assert_not_called()
    assert reconciliation.effects.mock_calls == []
    assert reconciliation.scope_events == ["enter", "exit"]


def test_github_unavailable_records_blocked_and_retains_pending_event(reconciliation):
    reconciliation.checks.get_pull_request.side_effect = OSError("offline")

    result = reconciliation.use_case.execute()
    assert len(result) == 1 and result[0].verdict == "BLOCKED"
    assert result[0].reason == "GITHUB_READ_UNAVAILABLE"
    assert reconciliation.effects.mock_calls == [
        call.require_owner(),
        call.record(result[0]),
        call.require_owner(),
    ]
    assert reconciliation.scope_events == ["enter", "exit"]


def test_leadership_loss_is_never_recorded_as_github_api_unavailable(reconciliation):
    reconciliation.checks.get_pull_request.side_effect = PermissionError(
        "leadership lost"
    )

    with pytest.raises(PermissionError, match="leadership lost"):
        reconciliation.use_case.execute()

    assert reconciliation.effects.mock_calls == []
    assert reconciliation.scope_events == ["enter", "exit"]


@pytest.mark.parametrize("verdict", ["SUCCESS", "FAILURE"])
def test_terminal_reconciliation_calls_both_use_cases_under_ownership(
    monkeypatch, reconciliation, verdict
):
    evidence = Mock(verdict=verdict)
    monkeypatch.setattr(reconcile_ci, "verify_ci", Mock(return_value=evidence))

    assert reconciliation.use_case.execute() == (evidence,)
    assert reconciliation.effects.mock_calls == [
        call.require_owner(),
        call.record(evidence),
        call.require_owner(),
        call.require_owner(),
        call.acknowledge(DELIVERY),
        call.require_owner(),
    ]
    assert reconciliation.scope_events == ["enter", "exit"]


@pytest.mark.parametrize(
    ("successful_checks", "last_call"),
    [(0, 1), (1, 3), (2, 4), (3, 6)],
    ids=["before-record", "after-record", "before-ack", "after-ack"],
)
def test_ownership_loss_stops_at_the_failing_write_boundary(
    monkeypatch, reconciliation, successful_checks, last_call
):
    evidence = Mock(verdict="SUCCESS")
    monkeypatch.setattr(reconcile_ci, "verify_ci", Mock(return_value=evidence))
    reconciliation.effects.require_owner.side_effect = [None] * successful_checks + [
        PermissionError("leadership lost")
    ]

    with pytest.raises(PermissionError, match="leadership lost"):
        reconciliation.use_case.execute()

    assert (
        reconciliation.effects.mock_calls
        == [
            call.require_owner(),
            call.record(evidence),
            call.require_owner(),
            call.require_owner(),
            call.acknowledge(DELIVERY),
            call.require_owner(),
        ][:last_call]
    )
    assert reconciliation.scope_events == ["enter", "exit"]
