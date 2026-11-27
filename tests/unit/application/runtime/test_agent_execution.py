"""Direct input and CI policy tests for the Application execution service."""

from contextlib import nullcontext
from dataclasses import replace
from unittest.mock import Mock

import pytest

from agent_workflow.application.dispatch.ports import DispatchClaim
from agent_workflow.application.exceptions import InvalidApplicationRequestError
from agent_workflow.application.runtime.dtos import RuntimePlan, RuntimeSpec
from agent_workflow.application.runtime.ports import (
    ICiTracking,
    IDispatchEvidence,
    IRuntimeEngine,
    IRuntimeRegistry,
    IRuntimeWorkspace,
    RuntimeAdapters,
)
from agent_workflow.application.runtime.services.agent_execution import (
    AgentExecutionResult,
    AgentExecutionService,
)
from agent_workflow.application.task.use_cases.get_coder_assignment import (
    CoderAssignmentOutput,
)
from agent_workflow.domain.exceptions import BusinessRuleError

CLAIM = DispatchClaim("a" * 32, "TASK", "b" * 40, 9_999_999_999.0, 0)
CONTEXT = {"task_id": "TASK"}
ASSIGNMENT = CONTEXT | {
    "details": CoderAssignmentOutput(Mock(task=Mock(task_id="TASK")), ())
}


@pytest.fixture
def dependencies():
    engine = Mock(spec=IRuntimeEngine)
    engine.bind_engine.return_value = nullcontext()
    workspace = Mock(spec=IRuntimeWorkspace)
    registry = Mock(spec=IRuntimeRegistry)
    registry.plan.return_value = RuntimePlan(("agent",))
    registry.approved_image_id.return_value = None
    return {
        "adapter_factory": Mock(return_value=RuntimeAdapters(engine, workspace)),
        "runtime_spec": Mock(return_value=RuntimeSpec("fake", "fixture", "model")),
        "registry": registry,
        "assignment_payload": Mock(return_value=CONTEXT),
        "dispatch": Mock(spec=IDispatchEvidence),
        "handoff_provider": Mock(),
        "ci": Mock(spec=ICiTracking),
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"claim": None},
        {"assignment": None},
        {"claim": {}},
        {"claim": replace(CLAIM, task_id=None)},
        {"claim": replace(CLAIM, task_id=""), "assignment": {"task_id": ""}},
        {"claim": replace(CLAIM, task_id=" TASK"), "assignment": {"task_id": " TASK"}},
        {"assignment": ASSIGNMENT | {"task_id": "OTHER"}},
        {"assignment": CONTEXT},
        {
            "assignment": ASSIGNMENT
            | {"details": CoderAssignmentOutput(Mock(task=Mock(task_id="OTHER")), ())}
        },
    ],
)
def test_invalid_execution_input_precedes_port_effects(dependencies, changes):
    guard = Mock()
    service = AgentExecutionService(**dependencies, require_ci_tracking=True)
    args = {"claim": CLAIM, "assignment": ASSIGNMENT, "guard": guard}
    with pytest.raises(InvalidApplicationRequestError, match="invalid agent execution"):
        service.execute(**(args | changes))
    assert all(not port.mock_calls for port in dependencies.values())
    guard.assert_not_called()


@pytest.mark.parametrize(
    "claim",
    [replace(CLAIM, dispatch_id="invalid"), replace(CLAIM, input_sha="invalid")],
)
def test_invalid_runtime_identity_precedes_adapter_construction(dependencies, claim):
    service = AgentExecutionService(**dependencies, require_ci_tracking=True)
    with pytest.raises(
        BusinessRuleError, match="invalid runtime request|40-character commit SHA"
    ):
        service.execute(claim, ASSIGNMENT, Mock())
    dependencies["adapter_factory"].assert_not_called()
    dependencies["registry"].plan.assert_not_called()


def test_runtime_request_is_bound_to_claim_and_invalid_plan_cannot_pin_engine(
    dependencies,
):
    registry = dependencies["registry"]
    registry.plan.return_value = object()
    engine = dependencies["adapter_factory"].return_value.engine
    service = AgentExecutionService(**dependencies, require_ci_tracking=True)
    with pytest.raises(InvalidApplicationRequestError, match="invalid agent execution"):
        service.execute(CLAIM, ASSIGNMENT, Mock())
    request = registry.plan.call_args.args[0]
    assert (request.dispatch_id, request.input_sha) == (
        CLAIM.dispatch_id,
        CLAIM.input_sha,
    )
    engine.bind_engine.assert_not_called()
    dependencies["dispatch"].record_runtime_evidence.assert_not_called()


def test_explicit_ci_policy_does_not_read_ambient_environment(
    monkeypatch, dependencies
):
    dependencies.pop("ci")
    factory = dependencies["adapter_factory"]
    workspace = factory.return_value.workspace
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "0")
    required = AgentExecutionService(**dependencies, require_ci_tracking=True)
    with pytest.raises(RuntimeError, match="CI tracking required"):
        required.execute(CLAIM, ASSIGNMENT, Mock())
    assert all(not port.mock_calls for port in dependencies.values())

    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "1")
    optional = AgentExecutionService(**dependencies, require_ci_tracking=False)
    workspace.prepare_stage.side_effect = RuntimeError("stage reached")
    with pytest.raises(RuntimeError, match="stage reached"):
        optional.execute(CLAIM, ASSIGNMENT, Mock())
    workspace.prepare_stage.assert_called_once_with(CLAIM, CONTEXT)
    assert dependencies["dispatch"].mock_calls == []


def test_ci_policy_must_be_explicit_boolean(dependencies):
    with pytest.raises(InvalidApplicationRequestError, match="explicit boolean"):
        AgentExecutionService(**dependencies, require_ci_tracking="0")
    assert all(not port.mock_calls for port in dependencies.values())


def test_terminal_cleanup_is_bound_to_the_same_dispatch_and_engine(dependencies):
    service = AgentExecutionService(**dependencies, require_ci_tracking=True)
    engine = dependencies["adapter_factory"].return_value.engine
    execution = AgentExecutionResult(CLAIM.dispatch_id, "c" * 40, engine)
    with pytest.raises(BusinessRuleError, match="differs from admitted Worker"):
        service.release_engine("other", execution)
    engine.release_engine.assert_not_called()
    service.release_engine(CLAIM.dispatch_id, execution)
    engine.release_engine.assert_called_once_with(CLAIM.dispatch_id)
