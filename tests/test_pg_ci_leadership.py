"""CI watcher ownership boundaries around state and blocking GitHub reads."""

from contextlib import contextmanager
from functools import partial
from types import SimpleNamespace
from unittest.mock import DEFAULT, MagicMock, Mock, call

import pytest
from test_pg_controller_leadership import Leader

from agent_workflow.app_config import AppConfig
from agent_workflow.application.ci.dtos import CiTrackedTaskSnapshot
from agent_workflow.application.exceptions import ApplicationError
from agent_workflow.composition import ci_deps
from agent_workflow.composition.ci_deps import compose_ci_execution
from agent_workflow.domain.ci_evidence import WebhookNotification
from agent_workflow.domain.ci_tracking import CiRecordSnapshot
from agent_workflow.infrastructure.ci import leadership_scope
from agent_workflow.infrastructure.ci.guarded_adapters import (
    GuardedCiChecks,
    GuardedCiReader,
)
from agent_workflow.infrastructure.database.session_factory import PostgresFactory
from agent_workflow.infrastructure.postgres_leadership import (
    LeadershipDomain,
)
from agent_workflow.interfaces.cli.ci_controller import (
    CiRunRequest,
    CiRunResponse,
    run_ci_pass,
)

REPO = "builacviet/agent-workflow"
SHA = "b" * 40
DELIVERY = "12345678-1234-1234-1234-123456789abc"


def _ci_inputs():
    reader = Mock()
    reader.pending_events.return_value = [
        WebhookNotification(DELIVERY, "workflow_run", "completed", REPO, 17, 31, SHA)
    ]
    reader.tracked.return_value = (CiTrackedTaskSnapshot("T", 31, SHA, "BLOCKED", 0),)
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


@pytest.mark.parametrize(
    ("operation", "args"),
    [
        ("pending_events", (20,)),
        ("tracked", (32,)),
        ("previous_failure", ("T", SHA)),
        ("get_pull_request", (REPO, 31)),
        ("list_check_runs", (REPO, SHA)),
    ],
)
def test_guarded_read_checks_ownership_before_and_after_io(operation, args):
    backend = Mock(installation_id=17)
    calls = Mock()
    read = getattr(backend, operation)
    calls.attach_mock(read, "read")
    reader = (
        GuardedCiChecks(backend, calls.require_owner)
        if operation in {"get_pull_request", "list_check_runs"}
        else GuardedCiReader(reader=backend, require_owner=calls.require_owner)
    )

    assert getattr(reader, operation)(*args) is read.return_value
    assert calls.mock_calls == [
        call.require_owner(),
        call.read(*args),
        call.require_owner(),
    ]

    calls.reset_mock()
    calls.require_owner.side_effect = PermissionError("leadership lost")
    with pytest.raises(PermissionError, match="leadership lost"):
        getattr(reader, operation)(*args)
    assert calls.mock_calls == [call.require_owner()]


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

    def mark_processed(delivery_id):
        # Model the durable ACK effect before the ownership post-check.
        ack(delivery_id)
        return True

    repository = Mock()
    repository.mark_processed.side_effect = mark_processed
    monkeypatch.setattr(
        ci_deps, "create_ci_state_adapters", Mock(return_value=(reader, repository))
    )

    @contextmanager
    def write_scope():
        tx = Mock()
        tx.load_for_record.return_value = CiRecordSnapshot(REPO, SHA, 31, SHA, False)
        uow = MagicMock()

        def persist(*args):
            if args[0] is None and uow.commit.called:
                record(*tx.record.call_args.args)

        uow.__exit__.side_effect = persist
        yield uow, tx

    monkeypatch.setattr(ci_deps, "PostgresCiWriteScope", Mock(return_value=write_scope))
    return watcher


def test_production_contention_fails_closed(production_watcher):
    watcher = production_watcher
    watcher.acquired = False

    with pytest.raises(ApplicationError, match="leadership unavailable"):
        with watcher.compose() as use_case:
            run_ci_pass(use_case, CiRunRequest(startup=True))

    assert watcher.effects.mock_calls == []
    (lock,) = watcher.locks
    assert lock.acquire_calls == 1
    assert lock.release_calls == 1
    watcher.factory.__exit__.assert_called_once()


def test_production_acquisition_error_propagates_and_releases(production_watcher):
    watcher = production_watcher
    watcher.acquisition_error = RuntimeError("lock acquisition failed")

    with pytest.raises(RuntimeError) as error:
        with watcher.compose() as use_case:
            run_ci_pass(use_case, CiRunRequest(startup=True))

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
    with watcher.compose() as use_case:
        response = run_ci_pass(use_case, CiRunRequest(startup=True))
        assert response == CiRunResponse(1, 0)
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
        response = run_ci_pass(use_case, CiRunRequest(startup=False))
        assert response == CiRunResponse(1, 0)
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
        with watcher.compose() as use_case:
            run_ci_pass(use_case, CiRunRequest(startup=True))

    # A write at the end of this prefix has already committed in the test double.
    assert [call[0] for call in watcher.effects.mock_calls] == (
        _PASS_EFFECTS[: _PASS_EFFECTS.index(lost_at) + 1]
    )
    (lock,) = watcher.locks
    assert lock.release_calls == 1
    watcher.factory.__exit__.assert_called_once()
