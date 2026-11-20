"""Authorize a task for the explicit local operator entrypoint."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from agent_workflow.application.shared.unit_of_work import IUnitOfWork
from agent_workflow.application.task.ports import ITaskAuthorizationRepository
from agent_workflow.domain.task.authorization import (
    TaskAuthorization,
    TaskAuthorizationSpec,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthorizeTaskCommand:
    spec: TaskAuthorizationSpec


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
            raise TypeError("a validated task authorization spec is required")
        spec = command.spec
        _LOGGER.info("task_authorization_started")
        with self._uow:
            existing = self._repository.load_for_authorization(spec.task_id)
            if existing is not None:
                spec.match_existing(existing)
            else:
                self._repository.add(
                    TaskAuthorization(spec, self._clock(), self._clock())
                )
                self._uow.commit()
        # The context owns the physical commit and can still fail after commit().
        _LOGGER.info(
            "task_authorization_committed"
            if existing is None
            else "task_authorization_skipped"
        )
