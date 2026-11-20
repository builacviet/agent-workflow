"""Ports for the bounded, local fixture operations."""

from typing import Protocol

from agent_workflow.domain.task.authorization import TaskAuthorizationSpec


class IAdminSeedRepository(Protocol):
    def import_history(self, spec: TaskAuthorizationSpec) -> None: ...

    def seed_synthetic(self, spec: TaskAuthorizationSpec) -> None: ...

    def reset_synthetic(self, spec: TaskAuthorizationSpec) -> None: ...
