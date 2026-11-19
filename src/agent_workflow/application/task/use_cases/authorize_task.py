"""Authorize a task for the explicit local operator entrypoint."""

import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass

from agent_workflow.application.shared.unit_of_work import IUnitOfWork
from agent_workflow.application.task.ports import ITaskAuthorizationRepository
from agent_workflow.domain.task.authorization import (
    TaskAuthorization,
    TaskAuthorizationSpec,
)
from agent_workflow.domain.workflow_identity import WorkflowError


@dataclass(frozen=True)
class AuthorizeTaskCommand:
    spec: TaskAuthorizationSpec


class AuthorizeTaskUseCase:
    def __init__(
        self,
        open_scope: Callable[
            [], AbstractContextManager[tuple[IUnitOfWork, ITaskAuthorizationRepository]]
        ],
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._open_scope = open_scope
        self._clock = clock

    def execute(self, command: AuthorizeTaskCommand) -> None:
        if (
            type(command) is not AuthorizeTaskCommand
            or type(command.spec) is not TaskAuthorizationSpec
        ):
            raise WorkflowError("a validated task authorization spec is required")
        spec = command.spec
        with self._open_scope() as (unit, repository):
            existing = repository.load_for_authorization(spec.task_id)
            if existing is not None:
                spec.match_existing(existing)
                return
            repository.add(TaskAuthorization(spec, self._clock(), self._clock()))
            unit.commit()
