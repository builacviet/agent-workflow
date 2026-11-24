"""Layer: Infrastructure
Responsibility: Persist review writes in the existing locked workflow transaction.
Dependencies: Review ports and PostgreSQL workflow unit.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager

from agent_workflow.domain.contracts import Submission
from agent_workflow.domain.review.policy import ClaimFacts, TaskFacts
from agent_workflow.storage.postgres_workflow import (
    PostgresWorkflowRepository,
    PostgresWorkflowUnit,
)


def encode(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def claim_facts(row: dict | None) -> ClaimFacts | None:
    if row is None:
        return None
    return ClaimFacts(
        row["token"],
        row["reviewer"],
        row["expected_sha"],
        row["expires_at"],
        row["status"],
        row["submitted_payload"],
    )


class PostgresReviewWrites:
    def __init__(self, unit: PostgresWorkflowUnit):
        self.unit = unit

    def lock_task(self, task_id: str) -> TaskFacts | None:
        row = self.unit.find_task((task_id,))
        if row is None:
            return None
        return TaskFacts(
            row["task_id"],
            row["status"],
            row["current_head_sha"],
            row["base_sha"],
            row["completed_review_rounds"],
            row["requested_effort"],
            bool(row["require_verified_effort"]),
        )

    def active_claim(self, task_id: str) -> ClaimFacts | None:
        return claim_facts(self.unit.find_active_claim((task_id,)))

    def find_claim(self, task_id: str, token: str) -> ClaimFacts | None:
        return claim_facts(self.unit.find_claim((token, task_id)))

    def latest_head_handoff_action(self, task_id: str, sha: str) -> str | None:
        handoff = self.unit.find_head_handoff(task_id, sha)
        return handoff["action"] if handoff else None

    def has_successful_ci(self, task_id: str, sha: str) -> bool:
        return self.unit.ci_approved((task_id, sha))

    def prior_findings(self, task_id: str) -> set[str]:
        return {
            finding["finding_id"]
            for review in self.unit.list_reviews((task_id,))
            for finding in self.unit.list_findings((review["review_id"],))
        }

    def transition(
        self, task: TaskFacts, status: str, action: str, now: float, metadata: dict
    ) -> None:
        self.unit.insert_event(
            (
                task.task_id,
                task.status,
                status,
                action,
                task.head,
                now,
                encode(metadata),
            )
        )
        self.unit.set_status((status, now, task.task_id))

    def expire(self, task: TaskFacts, claim: ClaimFacts, now: float) -> None:
        self.unit.expire_claim((claim.token,))
        self.transition(task, "READY_IN_REVIEW", "LEASE_EXPIRED", now, {})

    def claim(self, task: TaskFacts, claim: ClaimFacts, now: float) -> None:
        self.unit.insert_claim(
            (
                claim.token,
                task.task_id,
                claim.expected_sha,
                claim.reviewer,
                now,
                claim.expires_at,
            )
        )
        self.transition(task, "IN_REVIEW", "CLAIM", now, {})

    def submit(
        self,
        task: TaskFacts,
        submission: Submission,
        canonical: str,
        review_id: str,
        reviewer: str,
        now: float,
        actual: str | None,
        evidence: str,
    ) -> None:
        self.unit.insert_review(
            (
                review_id,
                task.task_id,
                submission.review_round,
                task.base,
                submission.reviewed_head_sha,
                submission.verdict,
                submission.summary,
                task.effort,
                actual,
                evidence,
                reviewer,
                now,
                None,
                encode(submission.prior_resolutions),
            )
        )
        for finding in submission.findings:
            self.unit.insert_finding(
                (
                    review_id,
                    finding.finding_id,
                    task.task_id,
                    finding.severity,
                    finding.acceptance_criterion,
                    finding.file_path,
                    finding.line,
                    finding.description,
                    finding.suggested_correction,
                    finding.required_verification,
                )
            )
        self.unit.set_rounds((submission.review_round, task.task_id))
        self.unit.submit_claim((canonical, submission.claim_token))
        self.transition(
            task,
            submission.verdict,
            "REVIEW_SUBMITTED",
            now,
            {"review_id": review_id, "round": submission.review_round},
        )


class PostgresReviewWriteScope:
    def __init__(self, repository: PostgresWorkflowRepository):
        self.repository = repository

    @contextmanager
    def __call__(self) -> Iterator[PostgresReviewWrites]:
        with self.repository.transaction() as unit:
            yield PostgresReviewWrites(unit)
