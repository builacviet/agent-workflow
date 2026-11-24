"""Layer: Application
Responsibility: Coordinate an atomic, authorized task handoff.
Dependencies: Unit of Work port, task handoff port and domain policy.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

from agent_workflow.application.shared.unit_of_work import IUnitOfWork
from agent_workflow.domain.exceptions import BusinessRuleError
from agent_workflow.domain.states import WorkflowStatus
from agent_workflow.domain.task.handoff import (
    HandoffRequest,
    HandoffTask,
    TaskEventAction,
    validate_handoff,
    validate_repair,
)


class IHandoffRepository(Protocol):
    def lock_task(self, task_id: str) -> HandoffTask | None: ...
    def handoff_path(
        self, task_id: str, sha: str | None
    ) -> tuple[bool, str | None]: ...
    def advance_failed_ci_head(self, task_id: str, old: str, new: str) -> int: ...
    def record(
        self,
        task: HandoffTask,
        request: HandoffRequest,
        action: TaskEventAction,
        metadata: dict[str, str | int],
        now: float,
    ) -> None: ...


@dataclass(frozen=True)
class HandoffResult:
    task_id: str
    status: WorkflowStatus
    head_sha: str


class RegisterTaskHandoffUseCase:
    def __init__(
        self,
        uow: IUnitOfWork,
        repository: IHandoffRepository,
        *,
        clock: Callable[[], float],
    ) -> None:
        self._uow = uow
        self._repository = repository
        self._clock = clock

    def execute(self, request: HandoffRequest) -> HandoffResult:
        if not isinstance(request, HandoffRequest):
            raise TypeError("handoff request required")
        request.validate()
        repo = self._repository
        with self._uow:
            task = repo.lock_task(request.task_id)
            if task is None:
                raise BusinessRuleError("unknown authorized task")
            validate_handoff(task, request)
            result = HandoffResult(
                task.task_id, WorkflowStatus.READY_IN_REVIEW, request.new_head_sha
            )
            action = TaskEventAction.HANDOFF
            metadata: dict[str, str | int] = {
                "handoff_file_path": request.handoff_file_path,
                "test_summary": request.test_summary,
                "verification": "reporter_supplied_not_remotely_verified",
            }
            if task.status == WorkflowStatus.READY_IN_REVIEW:
                exists, prior_path = repo.handoff_path(task.task_id, task.head_sha)
                if exists and prior_path != request.handoff_file_path:
                    raise BusinessRuleError("handoff path differs from current handoff")
                seen, _ = repo.handoff_path(task.task_id, request.new_head_sha)
                if not validate_repair(task, request, prior_path, seen):
                    return result
                assert task.head_sha is not None
                pr_number = repo.advance_failed_ci_head(
                    task.task_id, task.head_sha, request.new_head_sha
                )
                action = TaskEventAction.CI_REPAIR_HANDOFF
                metadata.update(previous_head_sha=task.head_sha, pr_number=pr_number)
            repo.record(task, request, action, metadata, self._clock())
            self._uow.commit()
            return result


HandoffUseCaseProvider = Callable[
    [], AbstractContextManager[RegisterTaskHandoffUseCase]
]
