"""CI watcher ownership boundaries around state and blocking GitHub reads."""

import os
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from test_pg_controller_leadership import Leader

from agent_workflow.composition.postgres_controller import PostgresCiWatcherDependencies
from agent_workflow.composition.postgres_leader_runner import run_ci_watcher_once
from agent_workflow.domain.ci_evidence import WebhookNotification
from agent_workflow.infrastructure.postgres_leadership import (
    LeadershipDomain,
    PostgresLeadershipLock,
)
from agent_workflow.storage.postgres_connection import PostgresFactory

REPO = "builacviet/agent-workflow"
SHA = "b" * 40
DELIVERY = "12345678-1234-1234-1234-123456789abc"


@pytest.fixture
def live_pg_factory():
    dsn = os.environ.get("PG_TEST_DSN")
    if not dsn:
        pytest.skip("disposable PG_TEST_DSN not supplied")
    if make_url(dsn).database not in {"workflow_test", "workflow_ci"}:
        pytest.fail("leadership tests require workflow_test or workflow_ci")
    engine = create_engine(
        dsn, connect_args={"connect_timeout": 5}, hide_parameters=True
    )
    with PostgresFactory(engine) as factory:
        with factory.transaction() as connection:
            assert (
                160000
                <= int(connection.scalar(text("SHOW server_version_num")))
                < 170000
            )
        yield factory


def fixture(acquired=True):
    leader = Leader(acquired)
    reader = Mock()
    reader.pending_events.return_value = [
        WebhookNotification(DELIVERY, "workflow_run", "completed", REPO, 17, 31, SHA)
    ]
    reader.tracked.return_value = [
        {
            "task_id": "T",
            "pr_number": 31,
            "head_sha": SHA,
        }
    ]
    reader.previous_failure.return_value = False
    recorder = Mock()
    acknowledger = Mock()
    checks = Mock()
    checks.installation_id = 17
    checks.get_pull_request.return_value = {
        "number": 31,
        "base": {"repo": {"full_name": REPO}},
        "head": {"sha": SHA},
    }
    checks.list_check_runs.return_value = {
        "total_count": 1,
        "check_runs": [
            {
                "id": 3,
                "name": "Python tests",
                "head_sha": SHA,
                "app": {"slug": "github-actions"},
                "status": "completed",
                "conclusion": "success",
                "completed_at": "2026-11-10T00:00:00Z",
                "event": "push",
                "workflow_path": ".github/workflows/v1-ci.yml",
                "tested_sha": SHA,
                "workflow_run_id": 44,
                "run_attempt": 1,
            }
        ],
    }
    return PostgresCiWatcherDependencies(reader, recorder, acknowledger, leader), checks


def test_denied_or_error_does_not_read_or_settle():
    deps, checks = fixture(False)
    assert run_ci_watcher_once(deps, checks, installation_id=17, startup=True) is None
    deps.reader.pending_events.assert_not_called()
    checks.get_pull_request.assert_not_called()
    deps.recorder.execute.assert_not_called()
    assert deps.leadership.release_calls == 1

    deps, checks = fixture()
    deps.leadership.try_acquire = Mock(side_effect=RuntimeError("offline"))
    with pytest.raises(RuntimeError):
        run_ci_watcher_once(deps, checks, installation_id=17)
    deps.reader.pending_events.assert_not_called()
    assert deps.leadership.release_calls == 1


def test_positive_iteration_records_and_acknowledges_then_retires():
    deps, checks = fixture()
    observed = run_ci_watcher_once(deps, checks, installation_id=17, startup=True)
    assert len(observed) == 1 and observed[0].verdict == "SUCCESS"
    deps.recorder.execute.assert_called_once()
    deps.acknowledger.execute.assert_called_once_with(DELIVERY)
    assert checks.get_pull_request.call_count == 2
    assert deps.leadership.release_calls == 1
    assert run_ci_watcher_once(deps, checks, installation_id=17) is None
    deps.recorder.execute.assert_called_once()


@pytest.mark.parametrize(
    "lost_at",
    ["pending_events", "tracked", "previous_failure", "pr", "checks", "record", "ack"],
)
def test_loss_stops_subsequent_ci_effects(lost_at):
    deps, checks = fixture()
    operation = {
        "pending_events": deps.reader.pending_events,
        "tracked": deps.reader.tracked,
        "previous_failure": deps.reader.previous_failure,
        "pr": checks.get_pull_request,
        "checks": checks.list_check_runs,
        "record": deps.recorder.execute,
        "ack": deps.acknowledger.execute,
    }[lost_at]
    original = operation.return_value

    def lose(*args, **kwargs):
        deps.leadership.held = False
        return original

    operation.side_effect = lose
    with pytest.raises(PermissionError, match="leadership lost"):
        run_ci_watcher_once(deps, checks, installation_id=17, startup=True)
    assert deps.leadership.release_calls == 1
    if lost_at in {"pending_events", "tracked", "previous_failure", "pr", "checks"}:
        deps.recorder.execute.assert_not_called()
    if lost_at != "ack":
        deps.acknowledger.execute.assert_not_called()


def test_domains_can_operate_independently():
    from test_pg_controller_leadership import fixture as controller_fixture

    from agent_workflow.composition.postgres_leader_runner import run_controller_once

    controller, assignment, execute = controller_fixture()
    watcher, checks = fixture()
    assert run_controller_once(controller, assignment, {"T"}, execute)
    assert run_ci_watcher_once(watcher, checks, installation_id=17)
    assert controller.leadership.acquire_calls == watcher.leadership.acquire_calls == 1
    assert controller.leadership is not watcher.leadership


def test_live_pinned_sessions_contend_and_separate_domains(live_pg_factory):
    controller_owner = PostgresLeadershipLock(
        live_pg_factory, LeadershipDomain.CONTROLLER
    )
    contender = PostgresLeadershipLock(live_pg_factory, LeadershipDomain.CONTROLLER)
    watcher = PostgresLeadershipLock(live_pg_factory, LeadershipDomain.CI_WATCHER)
    state = Mock()
    state.pending_events.return_value = []
    checks = Mock(installation_id=17)
    try:
        assert controller_owner.try_acquire()
        assert (
            run_ci_watcher_once(
                PostgresCiWatcherDependencies(
                    reader=state,
                    recorder=Mock(),
                    acknowledger=Mock(),
                    leadership=watcher,
                ),
                checks,
                installation_id=17,
            )
            == ()
        )
        assert not contender.try_acquire()
        state.pending_events.assert_called_once()
        checks.get_pull_request.assert_not_called()
        assert not watcher.try_acquire()  # Released instance is retired.
    finally:
        controller_owner.release()
        contender.release()
        watcher.release()
    successor = PostgresLeadershipLock(live_pg_factory, LeadershipDomain.CONTROLLER)
    try:
        assert successor.try_acquire()
    finally:
        successor.release()
