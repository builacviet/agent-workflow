"""Persistence for the local task authorization write use case."""

from typing import Protocol

from agent_workflow.domain.task.authorization import (
    TaskAuthorization,
    TaskAuthorizationSpec,
)


class ITaskAuthorizationRepository(Protocol):
    def load_for_authorization(self, task_id: str) -> TaskAuthorizationSpec | None:
        """Serialize concurrent authorization for this ID until the scope ends."""
        ...

    def add(self, authorization: TaskAuthorization) -> None:
        """Persist the Task and initial event together in the active scope."""
        ...
