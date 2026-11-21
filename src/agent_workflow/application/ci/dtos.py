"""Validated CI reconciliation request."""

from dataclasses import dataclass

from agent_workflow.application.exceptions import InvalidApplicationRequestError
from agent_workflow.domain.exceptions import DomainError
from agent_workflow.domain.workflow_identity import validate_sha


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
            raise InvalidApplicationRequestError("CI task id required")
        if not isinstance(self.repository, str) or not self.repository.strip():
            raise InvalidApplicationRequestError("CI repository required")
        if type(self.installation_id) is not int or self.installation_id < 1:
            raise InvalidApplicationRequestError("CI installation id invalid")
        if type(self.pr_number) is not int or self.pr_number < 1:
            raise InvalidApplicationRequestError("CI PR number invalid")
        try:
            validate_sha(self.expected_head_sha)
        except DomainError:
            raise InvalidApplicationRequestError("CI expected HEAD SHA invalid") from None
        if not isinstance(self.check_name, str) or not self.check_name.strip():
            raise InvalidApplicationRequestError("CI check name required")
        if not isinstance(self.check_app_slug, str) or not self.check_app_slug.strip():
            raise InvalidApplicationRequestError("CI check source required")
