"""Application-level CI tracking binding rule, independent of persistence."""

from agent_workflow.domain.workflow_identity import WorkflowError


def require_matching_ci_binding(
    persisted_pr: int,
    persisted_head: str,
    requested_pr: int,
    requested_head: str,
) -> None:
    """Do not rebind an existing CI task to a different PR or commit."""
    if persisted_pr != requested_pr:
        raise WorkflowError("CI tracking PR differs from persisted binding")
    if persisted_head != requested_head:
        raise WorkflowError("CI tracking HEAD differs from persisted binding")
