"""Run one CI pass only under an injected leadership port."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_workflow.application.ci.use_cases.run_ci_pass import (
    CiPassResult,
    RunCiPassUseCase,
)


class _Leadership:
    def __init__(self):
        self.events = []

    @contextmanager
    def acquired_scope(self):
        self.events.append("enter")
        try:
            yield
        finally:
            self.events.append("exit")


def test_pass_is_called_inside_scope_and_returns_observation_counts():
    leadership = _Leadership()
    evidence = tuple(
        SimpleNamespace(verdict=verdict)
        for verdict in ("SUCCESS", "BLOCKED", "FAILURE", "BLOCKED")
    )
    reconciler = Mock()

    def reconcile(*, startup):
        assert leadership.events == ["enter"]
        assert startup is True
        return evidence

    reconciler.execute.side_effect = reconcile
    assert RunCiPassUseCase(reconciler, leadership).execute(
        startup=True
    ) == CiPassResult(observed_count=4, blocked_count=2)
    assert leadership.events == ["enter", "exit"]


def test_empty_pass_returns_zero_observation_counts():
    reconciler = Mock()
    reconciler.execute.return_value = ()
    assert RunCiPassUseCase(reconciler, _Leadership()).execute() == CiPassResult(0, 0)


def test_pass_error_unwinds_scope_without_retry():
    leadership = _Leadership()
    reconciler = Mock()
    reconciler.execute.side_effect = PermissionError("lost")
    with pytest.raises(PermissionError, match="lost"):
        RunCiPassUseCase(reconciler, leadership).execute()
    reconciler.execute.assert_called_once_with(startup=False)
    assert leadership.events == ["enter", "exit"]
