"""Ownership scope: reuse injected dependencies but not a retired lock."""

from unittest.mock import Mock

import pytest

from agent_workflow.application.exceptions import ApplicationError
from agent_workflow.infrastructure.ci import leadership_scope


def _scope(monkeypatch, *, acquired=True):
    locks = []

    def create(factory, domain):
        lock = Mock()
        lock.try_acquire.return_value = acquired
        lock.is_held.return_value = True
        locks.append(lock)
        return lock

    monkeypatch.setattr(leadership_scope, "PostgresLeadershipLock", create)
    return leadership_scope.PostgresCiLeadershipScope(object()), locks


def test_each_pass_gets_fresh_leadership_and_always_releases(monkeypatch):
    scope, locks = _scope(monkeypatch)
    for _ in range(2):
        with scope.acquired_scope():
            scope.require_owner()
    assert len(locks) == 2
    assert locks[0] is not locks[1]
    assert all(lock.release.call_count == 1 for lock in locks)
    with pytest.raises(PermissionError, match="leadership lost"):
        scope.require_owner()


def test_denied_leadership_cannot_enter_application(monkeypatch):
    scope, locks = _scope(monkeypatch, acquired=False)
    with pytest.raises(ApplicationError, match="leadership unavailable"):
        with scope.acquired_scope():
            pytest.fail("side effect after denied leadership")
    assert locks[0].release.call_count == 1


def test_lost_owner_does_not_continue_reconciliation(monkeypatch):
    scope, locks = _scope(monkeypatch)
    with pytest.raises(PermissionError, match="leadership lost"):
        with scope.acquired_scope():
            locks[0].is_held.return_value = False
    assert locks[0].release.call_count == 1


@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt])
def test_exception_from_pass_releases_and_resets_lease(monkeypatch, error):
    scope, locks = _scope(monkeypatch)
    with pytest.raises(error, match="failure"):
        with scope.acquired_scope():
            raise error("failure")
    locks[0].release.assert_called_once()
    with pytest.raises(PermissionError):
        scope.require_owner()
