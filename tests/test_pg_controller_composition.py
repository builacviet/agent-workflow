"""Construction-only checks for the PostgreSQL Controller composition root."""

import socket
import sqlite3
import subprocess
from dataclasses import FrozenInstanceError
from unittest.mock import Mock

import pytest

from agent_workflow.application.ci.use_cases.acknowledge_ci_event import (
    AcknowledgeCiEventUseCase,
)
from agent_workflow.application.ci.use_cases.record_ci_evidence import (
    RecordCiEvidenceUseCase,
)
from agent_workflow.application.ci.use_cases.track_ci_task import TrackCiTaskUseCase
from agent_workflow.composition import postgres_controller as composition
from agent_workflow.infrastructure.postgres_leadership import (
    LeadershipDomain,
    PostgresLeadershipLock,
)
from agent_workflow.storage.postgres_connection import (
    PostgresFactory,
    PostgresUnavailableError,
)
from agent_workflow.storage.postgres_dispatch import PostgresDispatchLedger
from agent_workflow.storage.postgres_workflow import PostgresWorkflowRepository


def _opened_factory() -> PostgresFactory:
    # The real constructor checks schema and would require a database.
    factory = object.__new__(PostgresFactory)
    factory._closed = False
    factory._engine = Mock()
    return factory


def test_composition_borrows_factory_and_separates_leadership(monkeypatch):
    factory = _opened_factory()
    forbidden = Mock(side_effect=AssertionError("construction performed work"))
    for method in (
        "open",
        "assert_schema_ready",
        "connect_session",
        "transaction",
        "unit_of_work_session",
        "repository_session",
        "close",
    ):
        monkeypatch.setattr(PostgresFactory, method, forbidden)
    monkeypatch.setattr(PostgresLeadershipLock, "try_acquire", forbidden)
    monkeypatch.setattr(PostgresDispatchLedger, "claim", forbidden)
    monkeypatch.setattr(TrackCiTaskUseCase, "execute", forbidden)
    monkeypatch.setattr(RecordCiEvidenceUseCase, "execute", forbidden)
    monkeypatch.setattr(AcknowledgeCiEventUseCase, "execute", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)

    bundle = composition.compose_postgres_controller(factory)

    assert isinstance(bundle.controller.workflow, PostgresWorkflowRepository)
    assert bundle.controller.workflow.factory is factory
    assert isinstance(bundle.controller.dispatch, PostgresDispatchLedger)
    assert (
        bundle.controller.dispatch.factory,
        bundle.controller.dispatch.role,
        bundle.controller.dispatch.phase,
    ) == (
        factory,
        "dev",
        "code",
    )
    assert isinstance(bundle.controller.ci_tracking, TrackCiTaskUseCase)
    assert bundle.controller.ci_tracking._repository is bundle.controller.ci_repository
    assert bundle.controller.ci_factory is factory
    assert (
        bundle.ci_watcher.reader._session_scope
        is bundle.controller.ci_repository._session_scope
    )
    assert bundle.ci_watcher.recorder._repository is bundle.controller.ci_repository
    assert bundle.ci_watcher.acknowledger._repository is bundle.controller.ci_repository
    assert isinstance(bundle.controller.leadership, PostgresLeadershipLock)
    assert isinstance(bundle.ci_watcher.leadership, PostgresLeadershipLock)
    assert bundle.controller.leadership is not bundle.ci_watcher.leadership
    assert bundle.controller.leadership._key["domain"] == LeadershipDomain.CONTROLLER
    assert bundle.ci_watcher.leadership._key["domain"] == LeadershipDomain.CI_WATCHER
    assert bundle.controller.leadership._connection is None
    assert bundle.ci_watcher.leadership._connection is None
    assert factory._closed is False
    factory._engine.assert_not_called()
    forbidden.assert_not_called()
    for method in ("run", "start", "activate", "close"):
        assert not hasattr(bundle, method)
    with pytest.raises(FrozenInstanceError):
        bundle.controller = None


def test_invalid_or_closed_factory_fails_without_fallback():
    with pytest.raises(TypeError, match="opened PostgresFactory"):
        composition.compose_postgres_controller(object())

    factory = _opened_factory()
    factory.close()
    with pytest.raises(PostgresUnavailableError, match="factory is closed"):
        composition.compose_postgres_controller(factory)


def test_borrowed_factory_closure_fails_on_later_use():
    factory = _opened_factory()
    bundle = composition.compose_postgres_controller(factory)
    factory.close()

    with pytest.raises(PostgresUnavailableError, match="factory is closed"):
        with bundle.controller.workflow.read():
            pass
    assert bundle.controller.leadership._connection is None
    assert bundle.ci_watcher.leadership._connection is None


def test_constructor_failure_does_not_close_borrowed_factory(monkeypatch):
    factory = _opened_factory()
    close = Mock(side_effect=AssertionError("ownership transferred"))
    monkeypatch.setattr(factory, "close", close)

    def reject(*args, **kwargs):
        raise RuntimeError("adapter construction failed")

    monkeypatch.setattr(composition, "PostgresDispatchLedger", reject)
    with pytest.raises(RuntimeError, match="adapter construction failed"):
        composition.compose_postgres_controller(factory)
    close.assert_not_called()
    assert factory._closed is False
