"""Layer: Application
Responsibility: Run one dispatch under the Controller leadership fence.
Dependencies: Dispatch ports, execution budget, and Domain rules.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, ParamSpec, TypeVar

from agent_workflow.application.dispatch.ports import (
    ICoderAssignmentReader,
    IDispatchLeadership,
    IDispatchRepository,
)
from agent_workflow.application.exceptions import DispatchCleanupUnconfirmedError
from agent_workflow.application.task.use_cases.get_coder_assignment import (
    DispatchAssignment,
)
from agent_workflow.budget import dispatch_budget
from agent_workflow.domain.dispatch.policy import validate_dispatch_eligibility
from agent_workflow.domain.exceptions import BusinessRuleError

if TYPE_CHECKING:
    from agent_workflow.application.runtime.services.agent_execution import (
        AgentExecutionService,
    )

_P = ParamSpec("_P")
_T = TypeVar("_T")


def _fenced(
    guard: Callable[[], None],
    operation: Callable[_P, _T],
    /,
    *args: _P.args,
    **kwargs: _P.kwargs,
) -> _T:
    guard()
    result = operation(*args, **kwargs)
    guard()
    return result


class RunDispatchPassUseCase:
    def __init__(
        self,
        leadership: IDispatchLeadership,
        dispatch: IDispatchRepository,
        assignment: ICoderAssignmentReader[DispatchAssignment],
        agent_execution: AgentExecutionService,
    ) -> None:
        self._leadership = leadership
        self._dispatch = dispatch
        self._assignment = assignment
        self._agent_execution = agent_execution

    def execute(self, task_ids: set[str], timeout: int = 1800) -> bool:
        """Attempt one claim and give the Worker a guard for its own effects."""
        leadership = self._leadership

        def guard() -> None:
            if leadership.is_held() is not True:
                raise PermissionError(
                    "Dispatch leadership lost; reconcile in-flight work"
                )

        try:
            if not leadership.try_acquire():
                return False
            claim = _fenced(
                guard, self._dispatch.claim, task_ids, timeout, track_pre_launch=True
            )
            if claim is None:
                return False
            try:
                with dispatch_budget(claim.deadline):
                    context = _fenced(
                        guard, self._assignment.get_coder_assignment, claim.task_id
                    )
                    validate_dispatch_eligibility(
                        completed_review_rounds=context["completed_review_rounds"],
                        claimed_round=claim.round_number,
                        status=context["status"],
                        current_head_sha=context["current_head_sha"],
                        base_sha=context["base_sha"],
                        claimed_input_sha=claim.input_sha,
                    )
                    if (
                        claim.task_id not in task_ids
                        or context.get("task_id") != claim.task_id
                    ):
                        raise BusinessRuleError("dispatch outside exact task allowlist")
                    execution = _fenced(
                        guard, self._agent_execution.execute, claim, context, guard
                    )
                    _fenced(
                        guard,
                        self._dispatch.finish,
                        claim.dispatch_id,
                        execution.head_sha,
                    )
            except BaseException:
                # A lost owner leaves the active slot for operator reconciliation.
                if leadership.is_held() is True:
                    _fenced(guard, self._dispatch.block, claim.dispatch_id)
                raise
            # Finish has committed. Cleanup failure leaves it terminal and the
            # engine pin available for explicit operator recovery.
            try:
                _fenced(
                    guard,
                    self._agent_execution.release_engine,
                    claim.dispatch_id,
                    execution,
                )
            except Exception:
                raise DispatchCleanupUnconfirmedError(
                    "terminal dispatch cleanup unconfirmed; reconcile engine pin"
                ) from None
            return True
        finally:
            leadership.release()
