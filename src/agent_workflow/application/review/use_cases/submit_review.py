"""Layer: Application
Responsibility: Validate and atomically persist a review submission.
Dependencies: Unit of work, review write port and Domain policy.
"""

import json
from collections.abc import Callable

from agent_workflow.application.review.use_cases.write_contracts import (
    IReviewWriteRepository,
    ReviewSubmission,
    SubmitReviewInput,
    SubmitReviewOutput,
    ci_allows,
)
from agent_workflow.application.shared.unit_of_work import IUnitOfWork
from agent_workflow.domain.exceptions import BusinessRuleError
from agent_workflow.domain.review.policy import (
    TaskFacts,
    effort_allowed,
    submission_allowed,
    verdict_allowed,
)


class SubmitReviewUseCase:
    def __init__(
        self,
        uow: IUnitOfWork,
        repository: IReviewWriteRepository,
        *,
        clock: Callable[[], float],
        token: Callable[[], str],
        require_ci: bool,
        effort_verifier: Callable[
            [str, TaskFacts, ReviewSubmission], tuple[str | None, str] | None
        ]
        | None = None,
    ):
        self.uow = uow
        self.repository = repository
        self.clock, self.token, self.require_ci = clock, token, require_ci

        self.effort_verifier = effort_verifier

    def execute(self, request: SubmitReviewInput) -> SubmitReviewOutput:
        submission = request.submission
        resolutions = dict(submission.prior_resolutions)
        # These keys and defaults are part of the persisted idempotency identity.
        canonical = json.dumps(
            {
                "task_id": submission.task_id,
                "claim_token": submission.claim_token,
                "reviewed_head_sha": submission.reviewed_head_sha,
                "review_round": submission.review_round,
                "verdict": submission.verdict.value,
                "summary": submission.summary,
                "findings": [
                    {
                        "finding_id": finding.finding_id,
                        "severity": finding.severity,
                        "acceptance_criterion": finding.acceptance_criterion,
                        "file_path": finding.file_path,
                        "line": finding.line,
                        "description": finding.description,
                        "suggested_correction": finding.suggested_correction,
                        "required_verification": finding.required_verification,
                    }
                    for finding in submission.findings
                ],
                "prior_resolutions": resolutions,
                "requested_effort": submission.requested_effort,
                "actual_effort": submission.actual_effort,
                "verification_evidence": submission.verification_evidence,
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        repo = self.repository
        with self.uow:
            task = repo.lock_task(submission.task_id)
            if task is None:
                raise BusinessRuleError("unknown authorized task")
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
                    submission.verdict.value,
                    submission.reviewed_head_sha,
                    duplicate=True,
                )
            verdict_allowed(
                submission.review_round,
                submission.verdict,
                repo.prior_findings(task.task_id),
                resolutions,
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
            self.uow.commit()
            return SubmitReviewOutput(
                task.task_id,
                submission.verdict.value,
                submission.reviewed_head_sha,
                review_id=review_id,
                review_round=submission.review_round,
                actual_effort=actual,
            )
