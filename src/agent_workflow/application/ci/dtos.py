"""Validated CI reconciliation request."""

from dataclasses import dataclass

from agent_workflow.domain.workflow_identity import WorkflowError, validate_sha


@dataclass(frozen=True, slots=True)
class CiRequest:
    task_id: str
    repository: str
    installation_id: int
    pr_number: int
    expected_head_sha: str
    check_name: str
    check_app_slug: str = "github-actions"

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise WorkflowError("CI task id required")
        if not isinstance(self.repository, str) or not self.repository.strip():
            raise WorkflowError("CI repository required")
        if type(self.installation_id) is not int or self.installation_id < 1:
            raise WorkflowError("CI installation id invalid")
        if type(self.pr_number) is not int or self.pr_number < 1:
            raise WorkflowError("CI PR number invalid")
        validate_sha(self.expected_head_sha)
        if not isinstance(self.check_name, str) or not self.check_name.strip():
            raise WorkflowError("CI check name required")
        if not isinstance(self.check_app_slug, str) or not self.check_app_slug.strip():
            raise WorkflowError("CI check source required")
