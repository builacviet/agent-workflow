"""Layer: Application
Responsibility: Coordinate claim-scoped agent execution, evidence and terminal cleanup.
Dependencies: Runtime operation ports, typed assignments, handoff and dispatch budget.
"""

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from agent_workflow.application.dispatch.ports import DispatchClaim
from agent_workflow.application.exceptions import InvalidApplicationRequestError
from agent_workflow.application.runtime.dtos import (
    RuntimePlan,
    RuntimeRequest,
    RuntimeSpec,
)
from agent_workflow.application.runtime.ports import (
    ICiTracking,
    IDispatchEvidence,
    IRuntimeEngine,
    IRuntimeRegistry,
    RuntimeAdapterFactory,
    RuntimeAdapters,
)
from agent_workflow.application.task.use_cases.get_coder_assignment import (
    CoderAssignmentOutput,
    DispatchAssignment,
)
from agent_workflow.application.task.use_cases.register_task_handoff import (
    HandoffUseCaseProvider,
)
from agent_workflow.budget import check_deadline
from agent_workflow.domain.exceptions import BusinessRuleError
from agent_workflow.domain.task.handoff import HandoffRequest


@dataclass(frozen=True)
class AgentExecutionResult:
    dispatch_id: str
    head_sha: str
    engine: IRuntimeEngine


@contextmanager
def _phase(engine: IRuntimeEngine, dispatch_id: str) -> Iterator[None]:
    engine.require_leadership()
    try:
        yield
    finally:
        # An admitted phase may have launched even when it raises.
        engine.stop(dispatch_id)


class AgentExecutionService:
    def __init__(
        self,
        *,
        adapter_factory: RuntimeAdapterFactory,
        runtime_spec: Callable[[], RuntimeSpec],
        registry: IRuntimeRegistry,
        assignment_payload: Callable[[CoderAssignmentOutput], dict],
        dispatch: IDispatchEvidence,
        handoff_provider: HandoffUseCaseProvider,
        require_ci_tracking: bool,
        ci: ICiTracking | None = None,
    ) -> None:
        if type(require_ci_tracking) is not bool:
            raise InvalidApplicationRequestError("explicit boolean CI policy required")
        if (
            not callable(adapter_factory)
            or not callable(runtime_spec)
            or not callable(getattr(registry, "plan", None))
            or not callable(getattr(registry, "approved_image_id", None))
            or not callable(getattr(registry, "result", None))
            or not callable(assignment_payload)
            or not callable(getattr(dispatch, "record_runtime_evidence", None))
            or not callable(handoff_provider)
            or (ci is not None and not callable(getattr(ci, "execute", None)))
        ):
            raise TypeError(
                "Worker requires injected runtime, workflow and evidence operations"
            )
        self._adapter_factory = adapter_factory
        self._runtime_spec = runtime_spec
        self._registry = registry
        self._assignment_payload = assignment_payload
        self._dispatch = dispatch
        self._handoff_provider = handoff_provider
        self._require_ci_tracking = require_ci_tracking
        self._ci = ci

    def execute(
        self,
        claim: DispatchClaim,
        assignment: DispatchAssignment,
        guard: Callable[[], None],
    ) -> AgentExecutionResult:
        if not (
            isinstance(claim, DispatchClaim)
            and isinstance(assignment, dict)
            and isinstance(claim.task_id, str)
            and claim.task_id.strip()
            and claim.task_id == claim.task_id.strip()
            and claim.task_id == assignment.get("task_id")
            and isinstance(details := assignment.get("details"), CoderAssignmentOutput)
            and claim.task_id == details.context.task.task_id
            and callable(guard)
        ):
            raise InvalidApplicationRequestError("invalid agent execution input")
        if self._require_ci_tracking and self._ci is None:
            raise RuntimeError("CI tracking required but not configured")
        guard()
        check_deadline()
        request = RuntimeRequest(
            spec=self._runtime_spec(),
            role="dev",
            phase="code",
            dispatch_id=claim.dispatch_id,
            input_sha=claim.input_sha,
            required_capabilities=frozenset({"workspace-write"}),
        )
        adapters = self._adapter_factory(guard)
        engine = adapters.engine
        engine.require_leadership()
        context = self._assignment_payload(details)
        plan = self._registry.plan(request)
        if not isinstance(plan, RuntimePlan):
            raise InvalidApplicationRequestError("invalid agent execution input")
        approved_image_id = self._registry.approved_image_id(request)
        engine.validate_image(approved_image_id)
        with engine.bind_engine(claim.dispatch_id):
            head_sha = self._execute_phases(
                adapters, claim, context, request, plan, approved_image_id
            )
        return AgentExecutionResult(claim.dispatch_id, head_sha, engine)

    def _execute_phases(
        self,
        adapters: RuntimeAdapters,
        claim: DispatchClaim,
        context: dict,
        request: RuntimeRequest,
        plan: RuntimePlan,
        approved_image_id: str | None,
    ) -> str:
        engine, workspace = adapters.engine, adapters.workspace
        dispatch_id = claim.dispatch_id
        engine.require_leadership()
        stage, envelope = workspace.prepare_stage(claim, context)
        prepare_input = json.dumps(envelope)
        with _phase(engine, dispatch_id):
            engine.docker(claim, stage, "prepare", prepare_input)
        source = workspace.source(stage)
        prompt = (
            "Implement only this authorized task; do not commit/push/approve.\n"
            + json.dumps(context)
        )
        engine.require_leadership()
        engine.assert_engine(dispatch_id)
        with _phase(engine, dispatch_id):
            output, observed_image_id = engine.run_code(
                claim, source, plan, prompt, approved_image_id
            )
            runtime_result = self._registry.result(
                request, output, observed_image_id=observed_image_id
            )
        tested_digest = workspace.source_digest(source)
        with _phase(engine, dispatch_id):
            engine.docker(claim, source, "test")
        workspace.check_source_digest(source, tested_digest)
        test_summary = "python -m pytest -q: exit 0 in credential-free network-disabled test container"
        envelope["test_summary"] = test_summary
        publish_input = json.dumps(envelope)
        with _phase(engine, dispatch_id):
            engine.docker(claim, stage, "publish", publish_input)
        result = workspace.validated_result(stage, claim, context)
        check_deadline()
        engine.require_leadership()
        self._dispatch.record_runtime_evidence(
            dispatch_id,
            claim.task_id,
            claim.input_sha,
            request.spec.runtime,
            runtime_result.declared_runtime_version,
            runtime_result.observed_image_id,
            runtime_result.telemetry,
            tested_digest,
            result.head_sha,
        )
        engine.require_leadership()
        handoff_request = HandoffRequest(
            claim.task_id,
            context["repository"],
            context["branch"],
            context["base_sha"],
            result.head_sha,
            envelope["handoff_path"],
            test_summary + "; worker PR: " + result.pr_url,
        )
        with self._handoff_provider() as handoff:
            handoff.execute(handoff_request)
        # CI remains BLOCKED until the separate Controller re-verifies HEAD.
        if self._require_ci_tracking and self._ci is not None:
            engine.require_leadership()
            self._ci.execute(
                claim.task_id,
                int(result.pr_url.rsplit("/", 1)[1]),
                result.head_sha,
            )
        engine.require_leadership()
        return result.head_sha

    def release_engine(self, dispatch_id: str, execution: AgentExecutionResult) -> None:
        """Clear only the pin for this successful, now terminal dispatch."""
        if execution.dispatch_id != dispatch_id:
            raise BusinessRuleError("terminal dispatch differs from admitted Worker")
        execution.engine.release_engine(dispatch_id)
