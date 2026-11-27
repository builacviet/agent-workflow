"""Layer: Application
Responsibility: Coordinate trusted agent execution phases and evidence recording.
Dependencies: Runtime operation ports, DTOs and dispatch budget.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from agent_workflow.application.exceptions import InvalidApplicationRequestError
from agent_workflow.application.runtime.dtos import RuntimePlan, RuntimeRequest
from agent_workflow.application.runtime.ports import (
    ICiTracking,
    IDispatchEvidence,
    IRuntimeHost,
)
from agent_workflow.budget import check_deadline


class AgentExecutionService:
    """Coordinate one trusted dispatch using explicitly supplied state ports."""

    def __init__(
        self,
        host: IRuntimeHost,
        dispatch: IDispatchEvidence,
        ci: ICiTracking | None = None,
        leadership_guard: Callable[[], bool] | None = None,
        *,
        require_ci_tracking: bool,
    ) -> None:
        if type(require_ci_tracking) is not bool:
            raise InvalidApplicationRequestError("explicit boolean CI policy required")
        self.host = host
        self.dispatch = dispatch
        self.ci = ci
        self.leadership_guard = leadership_guard
        self.require_ci_tracking = require_ci_tracking

    def require_leadership(self) -> None:
        if self.leadership_guard is not None:
            check_deadline()
            if self.leadership_guard() is not True:
                raise PermissionError("runtime leadership guard rejected execution")
            check_deadline()

    @contextmanager
    def _phase(self, dispatch_id: str) -> Iterator[None]:
        self.require_leadership()
        try:
            yield
        finally:
            # An admitted phase may have launched even when it raises. Settle
            # its pinned resources once, including after loss or cancellation.
            self.host.stop(dispatch_id)

    def execute(
        self,
        claim: dict,
        context: dict,
        request: RuntimeRequest,
        plan: RuntimePlan,
        approved_image_id: str | None,
    ) -> str:
        if not (
            isinstance(claim, dict)
            and isinstance(context, dict)
            and isinstance(request, RuntimeRequest)
            and isinstance(plan, RuntimePlan)
            and claim.get("dispatch_id") == request.dispatch_id
            and claim.get("input_sha") == request.input_sha
            and isinstance(claim.get("task_id"), str)
            and claim["task_id"].strip()
            and claim["task_id"] == claim["task_id"].strip()
            and claim["task_id"] == context.get("task_id")
        ):
            raise InvalidApplicationRequestError("invalid agent execution input")
        if self.require_ci_tracking and self.ci is None:
            raise RuntimeError("CI tracking required but not configured")
        self.require_leadership()
        stage, envelope = self.host._prepare_stage(claim, context)
        dispatch_id = claim["dispatch_id"]
        prepare_input = json.dumps(envelope)
        with self._phase(dispatch_id):
            self.host.docker(claim, stage, "prepare", prepare_input)
        source = self.host._source(stage)
        prompt = (
            "Implement only this authorized task; do not commit/push/approve.\n"
            + json.dumps(context)
        )
        self.require_leadership()
        self.host._assert_engine(dispatch_id)
        with self._phase(dispatch_id):
            runtime_result = self.host._run_code(
                claim, source, request, plan, prompt, approved_image_id
            )
        tested_digest = self.host._source_digest(source)
        with self._phase(dispatch_id):
            self.host.docker(claim, source, "test")
        self.host._check_source_digest(source, tested_digest)
        envelope["test_summary"] = (
            "python -m pytest -q: exit 0 in credential-free network-disabled test container"
        )
        publish_input = json.dumps(envelope)
        with self._phase(dispatch_id):
            self.host.docker(claim, stage, "publish", publish_input)
        result = self.host._validated_result(stage, claim, context)
        check_deadline()
        self.require_leadership()
        self.dispatch.record_runtime_evidence(
            dispatch_id,
            context["task_id"],
            claim["input_sha"],
            request.spec.runtime,
            runtime_result.declared_runtime_version,
            runtime_result.observed_image_id,
            runtime_result.telemetry,
            tested_digest,
            result["head_sha"],
        )
        self.require_leadership()
        self.host._register_handoff(context, result, envelope["test_summary"])
        # CI remains BLOCKED until the separate Controller re-verifies HEAD.
        if self.require_ci_tracking and self.ci is not None:
            self.require_leadership()
            self.ci.execute(
                context["task_id"],
                int(result["pr_url"].rsplit("/", 1)[1]),
                result["head_sha"],
            )
        self.require_leadership()
        return result["head_sha"]
