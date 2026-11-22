"""CI watcher ownership boundaries around state and blocking GitHub reads."""

import os
from contextlib import contextmanager
from functools import partial
from types import SimpleNamespace
from unittest.mock import DEFAULT, MagicMock, Mock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from test_pg_controller_leadership import Leader

from agent_workflow.app_config import AppConfig
from agent_workflow.application.exceptions import ApplicationError
from agent_workflow.composition import ci_deps
from agent_workflow.composition.postgres_ci_entrypoint import compose_ci_execution
from agent_workflow.composition.postgres_controller import PostgresCiWatcherDependencies
from agent_workflow.composition.postgres_leader_runner import run_ci_watcher_once
from agent_workflow.domain.ci_evidence import WebhookNotification
from agent_workflow.domain.ci_tracking import CiRecordSnapshot
from agent_workflow.infrastructure.ci import leadership_scope
from agent_workflow.infrastructure.postgres_leadership import (
    LeadershipDomain,
    PostgresLeadershipLock,
)
from agent_workflow.interfaces.cli.ci_controller import CiRunRequest, CiRunResponse
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


def _ci_inputs():
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
    return reader, checks


def fixture(acquired=True):
    reader, checks = _ci_inputs()
    return (
        PostgresCiWatcherDependencies(reader, Mock(), Mock(), Leader(acquired)),
        checks,
    )


_PASS_EFFECTS = [
    "pending_events",
    "tracked",
    "previous_failure",
    "pr",
    "checks",
    "pr",
    "record",
    "ack",
]


@pytest.fixture
def production_watcher(monkeypatch):
    reader, checks = _ci_inputs()
    factory = MagicMock()
    factory.__enter__.return_value = factory
    monkeypatch.setattr(PostgresFactory, "open", Mock(return_value=factory))

    watcher = SimpleNamespace(
        acquired=True,
        acquisition_error=None,
        lose_after=None,
        locks=[],
        effects=Mock(),
        factory=factory,
        compose=partial(
            compose_ci_execution,
            {
                "controller_github_app": {
                    "app_id": 1,
                    "installation_id": 17,
                    "pem": "ci.pem",
                }
            },
            AppConfig(),
            checks=checks,
        ),
    )

    def create_lock(actual_factory, domain):
        assert actual_factory is factory
        assert domain is LeadershipDomain.CI_WATCHER
        lock = Leader(watcher.acquired)
        if watcher.acquisition_error is not None:
            lock.try_acquire = Mock(side_effect=watcher.acquisition_error)
        watcher.locks.append(lock)
        return lock

    monkeypatch.setattr(leadership_scope, "PostgresLeadershipLock", create_lock)

    def observe_effect(name):
        def execute(*args):
            lock = watcher.locks[-1]
            assert lock.acquire_calls == 1
            assert lock.is_held()
            if watcher.lose_after == name:
                lock.held = False
            return DEFAULT

        return execute

    record = Mock()
    ack = Mock()
    for name, operation in {
        "pending_events": reader.pending_events,
        "tracked": reader.tracked,
        "previous_failure": reader.previous_failure,
        "pr": checks.get_pull_request,
        "checks": checks.list_check_runs,
        "record": record,
        "ack": ack,
    }.items():
        watcher.effects.attach_mock(operation, name)
        operation.side_effect = observe_effect(name)

    @contextmanager
    def transaction():
        tx = Mock()
        tx.load_for_record.return_value = CiRecordSnapshot(REPO, SHA, 31, SHA, False)
        tx.inbox_delivery_exists.return_value = True
        yield tx
        # Model effects at commit, before the guarded adapter's ownership post-check.
        if tx.record.called:
            record(*tx.record.call_args.args)
        if tx.mark_processed.called:
            ack(*tx.mark_processed.call_args.args)

    repository = Mock()
    repository.transaction.side_effect = transaction
    monkeypatch.setattr(
        ci_deps, "create_ci_state_adapters", Mock(return_value=(reader, repository))
    )
    return watcher


def test_production_contention_fails_closed(production_watcher):
    watcher = production_watcher
    watcher.acquired = False

    with pytest.raises(ApplicationError, match="leadership unavailable"):
        with watcher.compose() as reconcile:
            reconcile(CiRunRequest(startup=True))

    assert watcher.effects.mock_calls == []
    (lock,) = watcher.locks
    assert lock.acquire_calls == 1
    assert lock.release_calls == 1
    watcher.factory.__exit__.assert_called_once()


def test_production_acquisition_error_propagates_and_releases(production_watcher):
    watcher = production_watcher
    watcher.acquisition_error = RuntimeError("lock acquisition failed")

    with pytest.raises(RuntimeError) as error:
        with watcher.compose() as reconcile:
            reconcile(CiRunRequest(startup=True))

    assert error.value is watcher.acquisition_error
    assert watcher.effects.mock_calls == []
    (lock,) = watcher.locks
    lock.try_acquire.assert_called_once_with()
    assert lock.release_calls == 1
    watcher.factory.__exit__.assert_called_once()


def test_production_pass_records_and_acknowledges_with_fresh_ownership(
    production_watcher,
):
    watcher = production_watcher
    with watcher.compose() as reconcile:
        assert reconcile(CiRunRequest(startup=True)) == CiRunResponse(1, 0)
        (first,) = watcher.locks
        assert first.release_calls == 1
        assert not first.is_held()
        watcher.effects.ack.assert_called_once_with(DELIVERY)
        evidence, verdict = watcher.effects.record.call_args.args
        assert (evidence.task_id, evidence.pr_number, evidence.expected_head_sha) == (
            "T",
            31,
            SHA,
        )
        assert evidence.verdict == verdict == "SUCCESS"

        # Give the second pass the same pending input to exercise every fence again.
        assert reconcile(CiRunRequest(startup=False)) == CiRunResponse(1, 0)
        _, second = watcher.locks
        assert second is not first
        assert first.release_calls == second.release_calls == 1
        assert not second.is_held()

    assert [call[0] for call in watcher.effects.mock_calls] == _PASS_EFFECTS * 2
    watcher.factory.__enter__.assert_called_once()
    watcher.factory.__exit__.assert_called_once()


@pytest.mark.parametrize("lost_at", list(dict.fromkeys(_PASS_EFFECTS)))
def test_production_loss_stops_after_material_operation(production_watcher, lost_at):
    watcher = production_watcher
    watcher.lose_after = lost_at

    with pytest.raises(PermissionError, match="leadership lost"):
        with watcher.compose() as reconcile:
            reconcile(CiRunRequest(startup=True))

    # A write at the end of this prefix has already committed in the test double.
    assert [call[0] for call in watcher.effects.mock_calls] == (
        _PASS_EFFECTS[: _PASS_EFFECTS.index(lost_at) + 1]
    )
    (lock,) = watcher.locks
    assert lock.release_calls == 1
    watcher.factory.__exit__.assert_called_once()


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
