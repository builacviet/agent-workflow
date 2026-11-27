"""Layer: Domain
Responsibility: Enforce the assignment facts required for dispatch.
Dependencies: Domain workflow statuses and errors.
"""

from agent_workflow.domain.exceptions import BusinessRuleError
from agent_workflow.domain.states import WorkflowStatus


def validate_dispatch_eligibility(
    *,
    status: str,
    completed_review_rounds: int,
    claimed_round: int,
    current_head_sha: str | None,
    base_sha: str,
    claimed_input_sha: str,
) -> None:
    if (
        completed_review_rounds != claimed_round
        or status
        not in {WorkflowStatus.AWAITING_HANDOFF, WorkflowStatus.CHANGES_REQUESTED}
        or (current_head_sha or base_sha) != claimed_input_sha
    ):
        raise BusinessRuleError("assignment changed after dispatch")
