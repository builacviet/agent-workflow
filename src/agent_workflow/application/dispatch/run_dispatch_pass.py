"""Run one dispatch under the Controller's existing leadership fence."""

from collections.abc import Callable
from typing import ParamSpec, TypeVar

from agent_workflow.application.dispatch.ports import (
    DispatchExecution,
    ICoderAssignmentReader,
    IDispatchLeadership,
    IDispatchRepository,
)
from agent_workflow.budget import dispatch_budget
from agent_workflow.domain.states import WorkflowStatus
from agent_workflow.domain.workflow_identity import WorkflowError

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
        assignment: ICoderAssignmentReader,
        execute_worker: DispatchExecution,
        *,
        on_finished: Callable[[str], None] | None = None,
    ) -> None:
        self._leadership = leadership
        self._dispatch = dispatch
        self._assignment = assignment
        self._execute_worker = execute_worker
        self._on_finished = on_finished

    def execute(self, task_ids: set[str], timeout: int = 1800) -> bool:
        """Attempt one claim and give the Worker a guard for its own effects."""
        leadership = self._leadership

        def guard() -> None:
            if leadership.is_held() is not True:
                raise PermissionError(
                    "PostgreSQL leadership lost; reconcile in-flight work"
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
                with dispatch_budget(claim["deadline"]):
                    context = _fenced(
                        guard, self._assignment.get_coder_assignment, claim["task_id"]
                    )
                    if (
                        context["completed_review_rounds"] != claim["round_number"]
                        or context["status"]
                        not in {
                            WorkflowStatus.AWAITING_HANDOFF,
                            WorkflowStatus.CHANGES_REQUESTED,
                        }
                        or (context["current_head_sha"] or context["base_sha"])
                        != claim["input_sha"]
                    ):
                        raise WorkflowError("assignment changed after dispatch")
                    sha = _fenced(guard, self._execute_worker, claim, context, guard)
                    _fenced(guard, self._dispatch.finish, claim["dispatch_id"], sha)
            except BaseException:
                # A lost owner leaves the active slot for operator reconciliation.
                if leadership.is_held() is True:
                    _fenced(guard, self._dispatch.block, claim["dispatch_id"])
                raise
            # Finish has committed. Cleanup failure leaves it terminal and the
            # engine pin available for explicit operator recovery.
            if self._on_finished is not None:
                try:
                    _fenced(guard, self._on_finished, claim["dispatch_id"])
                except Exception:
                    raise WorkflowError(
                        "terminal dispatch cleanup unconfirmed; reconcile engine pin"
                    ) from None
            return True
        finally:
            leadership.release()
