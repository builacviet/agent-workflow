"""Atomic entry points for the two fixtures and explicit synthetic reset."""

from dataclasses import dataclass

from agent_workflow.application.admin.ports import IAdminSeedRepository
from agent_workflow.application.shared.unit_of_work import IUnitOfWork
from agent_workflow.domain.task.authorization import TaskAuthorizationSpec
from agent_workflow.domain.workflow_identity import WorkflowError


@dataclass(frozen=True)
class AdminSeedCommand:
    spec: TaskAuthorizationSpec


def _validate(command: AdminSeedCommand, task_id: str, synthetic: bool) -> None:
    if (
        type(command) is not AdminSeedCommand
        or type(command.spec) is not TaskAuthorizationSpec
        or command.spec.task_id != task_id
        or command.spec.synthetic is not synthetic
    ):
        raise WorkflowError("invalid admin fixture authorization")


class ImportHistoricalReviewsUseCase:
    def __init__(self, uow: IUnitOfWork, repository: IAdminSeedRepository) -> None:
        self._uow = uow
        self._repository = repository

    def execute(self, command: AdminSeedCommand) -> None:
        _validate(command, "TASK-001", False)
        with self._uow:
            self._repository.import_history(command.spec)
            self._uow.commit()


class SeedSyntheticReviewUseCase:
    def __init__(self, uow: IUnitOfWork, repository: IAdminSeedRepository) -> None:
        self._uow = uow
        self._repository = repository

    def execute(self, command: AdminSeedCommand) -> None:
        _validate(command, "POC-REVIEW-001", True)
        with self._uow:
            self._repository.seed_synthetic(command.spec)
            self._uow.commit()


class ResetSyntheticReviewUseCase:
    def __init__(self, uow: IUnitOfWork, repository: IAdminSeedRepository) -> None:
        self._uow = uow
        self._repository = repository

    def execute(self, command: AdminSeedCommand) -> None:
        _validate(command, "POC-REVIEW-001", True)
        with self._uow:
            self._repository.reset_synthetic(command.spec)
            self._repository.seed_synthetic(command.spec)
            self._uow.commit()
