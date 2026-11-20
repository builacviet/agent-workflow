"""Authorize a task for the explicit local operator entrypoint."""

import time
from collections.abc import Callable
from dataclasses import dataclass, fields

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


def validate_authorization_text(spec: TaskAuthorizationSpec) -> None:
    """Reject text PostgreSQL clients cannot safely transmit or persist."""
    for field in fields(spec):
        value = getattr(spec, field.name)
        strings = (
            (value,)
            if isinstance(value, str)
            else value
            if isinstance(value, tuple)
            else ()
        )
        for item in strings:
            if "\0" in item:
                raise WorkflowError(f"{field.name} must not contain NUL")
            try:
                item.encode("utf-8")
            except UnicodeEncodeError:
                raise WorkflowError(f"{field.name} must be valid UTF-8") from None


class AuthorizeTaskUseCase:
    def __init__(
        self,
        uow: IUnitOfWork,
        repository: ITaskAuthorizationRepository,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._uow = uow
        self._repository = repository
        self._clock = clock

    def execute(self, command: AuthorizeTaskCommand) -> None:
        if (
            type(command) is not AuthorizeTaskCommand
            or type(command.spec) is not TaskAuthorizationSpec
        ):
            raise WorkflowError("a validated task authorization spec is required")
        spec = command.spec
        validate_authorization_text(spec)
        with self._uow:
            existing = self._repository.load_for_authorization(spec.task_id)
            if existing is not None:
                spec.match_existing(existing)
                return
            self._repository.add(TaskAuthorization(spec, self._clock(), self._clock()))
            self._uow.commit()
