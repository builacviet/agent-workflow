"""Approve only durable verified SUCCESS bound to the exact current HEAD."""

from agent_workflow.application.ci.ports import ICiApprovalReader


class CheckCiApprovalUseCase:
    def __init__(self, reader: ICiApprovalReader) -> None:
        self._reader = reader

    def execute(self, task_id: str, head_sha: str) -> bool:
        state = self._reader.approved(task_id, head_sha)
        return state.approves(head_sha) if state is not None else False
