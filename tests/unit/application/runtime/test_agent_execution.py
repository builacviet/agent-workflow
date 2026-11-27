"""Direct input and CI policy tests for the Application execution service."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from agent_workflow.application.exceptions import InvalidApplicationRequestError
from agent_workflow.application.runtime.dtos import (
    RuntimePlan,
    RuntimeRequest,
    RuntimeSpec,
)
from agent_workflow.application.runtime.ports import (
    ICiTracking,
    IDispatchEvidence,
    IRuntimeHost,
)
from agent_workflow.application.runtime.services.agent_execution import (
    AgentExecutionService,
)

CLAIM = {"dispatch_id": "a" * 32, "input_sha": "b" * 40, "task_id": "TASK"}
CONTEXT = {"task_id": "TASK"}
REQUEST = RuntimeRequest(
    spec=RuntimeSpec("fake", "fixture", "model"),
    role="dev",
    phase="code",
    dispatch_id=CLAIM["dispatch_id"],
    input_sha=CLAIM["input_sha"],
)
PLAN = RuntimePlan(("agent",))


@pytest.mark.parametrize(
    "changes",
    [
        {"claim": None},
        {"context": None},
        {"claim": {}},
        {"claim": CLAIM | {"dispatch_id": "c" * 32}},
        {"claim": CLAIM | {"input_sha": "c" * 40}},
        {"claim": CLAIM | {"task_id": None}},
        {"claim": CLAIM | {"task_id": ""}, "context": {"task_id": ""}},
        {"claim": CLAIM | {"task_id": " TASK"}, "context": {"task_id": " TASK"}},
        {"context": {"task_id": "OTHER"}},
        {"request": object()},
        {"request": replace(REQUEST, input_sha="c" * 40)},
        {"plan": object()},
    ],
)
def test_invalid_execution_input_precedes_port_effects(changes):
    host = Mock(spec=IRuntimeHost)
    dispatch = Mock(spec=IDispatchEvidence)
    ci = Mock(spec=ICiTracking)
    guard = Mock(return_value=True)
    service = AgentExecutionService(host, dispatch, ci, guard, require_ci_tracking=True)
    args = {
        "claim": CLAIM,
        "context": CONTEXT,
        "request": REQUEST,
        "plan": PLAN,
        "approved_image_id": None,
    }
    with pytest.raises(InvalidApplicationRequestError, match="invalid agent execution"):
        service.execute(**(args | changes))
    assert host.mock_calls == dispatch.mock_calls == ci.mock_calls == []
    guard.assert_not_called()


def test_explicit_ci_policy_does_not_read_ambient_environment(monkeypatch):
    host = Mock(spec=IRuntimeHost)
    dispatch = Mock(spec=IDispatchEvidence)
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "0")
    required = AgentExecutionService(host, dispatch, require_ci_tracking=True)
    with pytest.raises(RuntimeError, match="CI tracking required"):
        required.execute(CLAIM, CONTEXT, REQUEST, PLAN, None)
    assert host.mock_calls == dispatch.mock_calls == []

    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "1")
    optional = AgentExecutionService(host, dispatch, require_ci_tracking=False)
    host._prepare_stage.side_effect = RuntimeError("stage reached")
    with pytest.raises(RuntimeError, match="stage reached"):
        optional.execute(CLAIM, CONTEXT, REQUEST, PLAN, None)
    host._prepare_stage.assert_called_once_with(CLAIM, CONTEXT)
    assert dispatch.mock_calls == []


def test_ci_policy_must_be_explicit_boolean():
    host = Mock(spec=IRuntimeHost)
    dispatch = Mock(spec=IDispatchEvidence)
    with pytest.raises(InvalidApplicationRequestError, match="explicit boolean"):
        AgentExecutionService(host, dispatch, require_ci_tracking="0")
    assert host.mock_calls == dispatch.mock_calls == []
