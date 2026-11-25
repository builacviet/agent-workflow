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
from agent_workflow.composition.task_handoff_deps import PostgresHandoffProvider
from agent_workflow.infrastructure.ci.leadership_scope import PostgresCiLeadershipScope
from agent_workflow.infrastructure.database.repositories.ci_state_reader import (
    SqlAlchemyCiStateReader,
)
from agent_workflow.infrastructure.database.repositories.ci_state_repository import (
    SqlAlchemyCiStateRepository,
)
from agent_workflow.infrastructure.database.repositories.dispatch_ledger import (
    PostgresDispatchLedger,
)
from agent_workflow.infrastructure.database.session_factory import (
    PostgresFactory,
    PostgresUnavailableError,
)
from agent_workflow.infrastructure.postgres_leadership import (
    LeadershipDomain,
    PostgresLeadershipLock,
)


def _opened_factory() -> PostgresFactory:
    # The real constructor checks schema and would require a database.
    factory = object.__new__(PostgresFactory)
    factory._closed = False
    factory._engine = Mock()
    return factory


def test_composition_borrows_factory_and_builds_only_dispatch_dependencies(monkeypatch):
    factory = _opened_factory()
    forbidden = Mock(side_effect=AssertionError("construction performed work"))
    for method in (
        "open",
        "assert_schema_ready",
        "connect_session",
        "get_scoped_session",
        "transaction",
        "unit_of_work_session",
        "repository_session",
        "close",
    ):
        monkeypatch.setattr(PostgresFactory, method, forbidden)
    monkeypatch.setattr(PostgresLeadershipLock, "try_acquire", forbidden)
    monkeypatch.setattr(PostgresDispatchLedger, "claim", forbidden)
    monkeypatch.setattr(TrackCiTaskUseCase, "execute", forbidden)
    for watcher_component in (
        SqlAlchemyCiStateReader,
        RecordCiEvidenceUseCase,
        AcknowledgeCiEventUseCase,
        PostgresCiLeadershipScope,
    ):
        monkeypatch.setattr(watcher_component, "__init__", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)

    constructed_locks = Mock()
    init_lock = PostgresLeadershipLock.__init__

    def track_lock(lock, lock_factory, domain):
        constructed_locks(lock_factory, domain)
        init_lock(lock, lock_factory, domain)

    monkeypatch.setattr(PostgresLeadershipLock, "__init__", track_lock)
    dependencies = composition.compose_postgres_controller(factory)

    assert isinstance(dependencies, composition.ControllerDependencies)
    assert isinstance(dependencies.handoff_provider, PostgresHandoffProvider)
    assert dependencies.handoff_provider.factory is factory
    assert isinstance(dependencies.dispatch, PostgresDispatchLedger)
    assert (
        dependencies.dispatch.factory,
        dependencies.dispatch.role,
        dependencies.dispatch.phase,
    ) == (
        factory,
        "dev",
        "code",
    )
    assert isinstance(dependencies.ci_tracking, TrackCiTaskUseCase)
    assert isinstance(dependencies.ci_repository, SqlAlchemyCiStateRepository)
    assert dependencies.ci_tracking._repository is dependencies.ci_repository
    assert dependencies.ci_factory is factory
    assert isinstance(dependencies.leadership, PostgresLeadershipLock)
    constructed_locks.assert_called_once_with(factory, LeadershipDomain.CONTROLLER)
    assert dependencies.leadership._key["domain"] == LeadershipDomain.CONTROLLER
    assert dependencies.leadership._connection is None
    assert factory._closed is False
    factory._engine.assert_not_called()
    forbidden.assert_not_called()
    for method in ("run", "start", "activate", "close"):
        assert not hasattr(dependencies, method)
    with pytest.raises(FrozenInstanceError):
        dependencies.handoff_provider = None

    scoped_session = Mock()
    monkeypatch.setattr(factory, "get_scoped_session", scoped_session)
    assert dependencies.ci_repository._session_scope() is scoped_session.return_value
    scoped_session.assert_called_once_with()


def test_controller_composition_does_not_expose_obsolete_watcher_path():
    assert not hasattr(composition, "PostgresCiWatcherDependencies")


def test_invalid_or_closed_factory_fails_without_fallback():
    with pytest.raises(TypeError, match="opened PostgresFactory"):
        composition.compose_postgres_controller(object())

    factory = _opened_factory()
    factory.close()
    with pytest.raises(PostgresUnavailableError, match="factory is closed"):
        composition.compose_postgres_controller(factory)


def test_borrowed_factory_closure_fails_on_later_use():
    factory = _opened_factory()
    dependencies = composition.compose_postgres_controller(factory)
    factory.close()

    with pytest.raises(PostgresUnavailableError, match="factory is closed"):
        with dependencies.handoff_provider():
            pass
    assert dependencies.leadership._connection is None


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
