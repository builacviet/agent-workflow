"""Authorize one immutable CI task/PR/HEAD binding in a single transaction."""

from agent_workflow.application.ci.ports import ICiWriteRepository
from agent_workflow.application.exceptions import InvalidApplicationRequestError
from agent_workflow.domain.exceptions import DomainError
from agent_workflow.domain.workflow_identity import validate_sha


class TrackCiTaskUseCase:
    def __init__(self, repository: ICiWriteRepository) -> None:
        self._repository = repository

    def execute(self, task_id: str, pr_number: int, head_sha: str) -> None:
        try:
            validate_sha(head_sha)
        except DomainError:
            raise InvalidApplicationRequestError("invalid CI tracking HEAD") from None
        if (
            not isinstance(task_id, str)
            or not task_id
            or type(pr_number) is not int
            or not 1 <= pr_number < 2**31
        ):
            raise InvalidApplicationRequestError("invalid CI tracking identity")
        with self._repository.transaction() as tx:
            snapshot = tx.load_for_tracking(task_id)
            if snapshot.needs_binding(pr_number, head_sha):
                tx.track(task_id, pr_number, head_sha)
