"""Ports for the bounded, local fixture operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from agent_workflow.domain.task.authorization import TaskAuthorizationSpec

if TYPE_CHECKING:
    from agent_workflow.application.admin.use_cases import AdminSeedCommand


class IAdminSeedRepository(Protocol):
    def import_history(self, spec: TaskAuthorizationSpec) -> None: ...

    def seed_synthetic(self, spec: TaskAuthorizationSpec) -> None: ...

    def reset_synthetic(self, spec: TaskAuthorizationSpec) -> None: ...


class IAdminSeedUseCase(Protocol):
    def execute(self, command: AdminSeedCommand) -> None: ...
