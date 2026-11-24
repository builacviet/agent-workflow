"""Layer: Application
Responsibility: Atomically claim an eligible review at its current head.
Dependencies: Review write port and Domain policy.
"""

from collections.abc import Callable
from dataclasses import replace

from agent_workflow.application.review.use_cases.write_contracts import (
    ClaimReviewInput,
    ClaimReviewOutput,
    IReviewWriteScope,
    ci_allows,
)
from agent_workflow.domain.review.policy import ClaimFacts, claim_allowed
from agent_workflow.domain.states import WorkflowStatus
from agent_workflow.domain.workflow_identity import WorkflowError, validate_sha


class ClaimReviewUseCase:
    def __init__(
        self,
        scope: IReviewWriteScope,
        *,
        clock: Callable[[], float],
        token: Callable[[], str],
        require_ci: bool,
        lease_seconds: int = 900,
    ):
        self.scope, self.clock, self.token = scope, clock, token
        self.require_ci, self.lease_seconds = require_ci, lease_seconds

    def execute(self, request: ClaimReviewInput) -> ClaimReviewOutput:
        if not isinstance(request.task_id, str) or not request.task_id.strip():
            raise WorkflowError("unknown authorized task")
        if not isinstance(request.reviewer, str) or not request.reviewer.strip():
            raise WorkflowError("invalid reviewer")
        validate_sha(request.expected_head_sha)
        with self.scope() as repo:
            task = repo.lock_task(request.task_id)
            if task is None:
                raise WorkflowError("unknown authorized task")
            active = repo.active_claim(task.task_id)
            now = self.clock()
            if active and active.expires_at <= now:
                repo.expire(task, active, now)
                task = replace(task, status=WorkflowStatus.READY_IN_REVIEW)
                active = None
            claim_allowed(
                task,
                request.expected_head_sha,
                ci_allows(
                    repo, task.task_id, request.expected_head_sha, self.require_ci
                ),
                active is not None,
            )
            claim = ClaimFacts(
                self.token(),
                request.reviewer,
                request.expected_head_sha,
                now + self.lease_seconds,
                "ACTIVE",
                None,
            )
            repo.claim(task, claim, now)
            return ClaimReviewOutput(
                claim.token,
                task.task_id,
                claim.expected_sha,
                task.rounds + 1,
                claim.expires_at,
            )
