"""Layer: Application
Responsibility: Validate and atomically persist a review submission.
Dependencies: Review write port and Domain policy.
"""

import json
from collections.abc import Callable

from agent_workflow.application.review.use_cases.write_contracts import (
    IReviewWriteScope,
    SubmitReviewInput,
    SubmitReviewOutput,
    ci_allows,
)
from agent_workflow.domain.contracts import Submission
from agent_workflow.domain.review.policy import (
    TaskFacts,
    effort_allowed,
    submission_allowed,
    verdict_allowed,
)
from agent_workflow.domain.workflow_identity import WorkflowError, validate_sha


class SubmitReviewUseCase:
    def __init__(
        self,
        scope: IReviewWriteScope,
        *,
        clock: Callable[[], float],
        token: Callable[[], str],
        require_ci: bool,
        effort_verifier: Callable[
            [str, TaskFacts, Submission], tuple[str | None, str] | None
        ]
        | None = None,
    ):
        self.scope, self.clock, self.token, self.require_ci = (
            scope,
            clock,
            token,
            require_ci,
        )

        self.effort_verifier = effort_verifier

    def execute(self, request: SubmitReviewInput) -> SubmitReviewOutput:
        submission = Submission.model_validate(request.submission)
        if not submission.task_id.strip() or not submission.claim_token.strip():
            raise WorkflowError("unknown claim or claim owner mismatch")
        if not isinstance(request.reviewer, str) or not request.reviewer.strip():
            raise WorkflowError("invalid reviewer")
        validate_sha(submission.reviewed_head_sha)
        canonical = json.dumps(
            submission.model_dump(),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        with self.scope() as repo:
            task = repo.lock_task(submission.task_id)
            if task is None:
                raise WorkflowError("unknown authorized task")
            claim = repo.find_claim(task.task_id, submission.claim_token)
            duplicate = submission_allowed(
                task,
                claim,
                request.reviewer,
                self.clock(),
                canonical,
                submission.reviewed_head_sha,
                submission.review_round,
                submission.requested_effort,
                True
                if claim is None or claim.status == "SUBMITTED"
                else ci_allows(
                    repo, task.task_id, submission.reviewed_head_sha, self.require_ci
                ),
            )
            if duplicate:
                return SubmitReviewOutput(
                    task.task_id,
                    submission.verdict,
                    submission.reviewed_head_sha,
                    duplicate=True,
                )
            verdict_allowed(
                submission.review_round,
                submission.verdict,
                repo.prior_findings(task.task_id),
                submission.prior_resolutions,
                [f.finding_id for f in submission.findings],
            )
            actual, evidence = None, "unverified"
            if self.effort_verifier:
                verified = self.effort_verifier(request.reviewer, task, submission)
                if verified:
                    actual, evidence = verified
            effort_allowed(task, submission.verdict, actual, evidence)
            review_id = self.token()
            repo.submit(
                task,
                submission,
                canonical,
                review_id,
                request.reviewer,
                self.clock(),
                actual,
                evidence,
            )
            return SubmitReviewOutput(
                task.task_id,
                submission.verdict,
                submission.reviewed_head_sha,
                review_id=review_id,
                review_round=submission.review_round,
                actual_effort=actual,
            )
