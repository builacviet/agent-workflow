"""Layer: Infrastructure
Responsibility: Persist claim and submit operations in the caller's transaction.
Dependencies: Review write contracts, canonical PostgreSQL models and SQLAlchemy Session.
"""

import json

from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from agent_workflow.application.exceptions import CorruptedPersistenceDataError
from agent_workflow.application.review.use_cases.write_contracts import ReviewSubmission
from agent_workflow.domain.review.events import ReviewEventAction
from agent_workflow.domain.review.policy import ClaimFacts, TaskFacts
from agent_workflow.domain.states import CiVerdict, WorkflowStatus
from agent_workflow.domain.task.handoff import TaskEventAction
from agent_workflow.infrastructure.database.models import (
    CITask,
    Review,
    ReviewAttempt,
    ReviewFinding,
    Task,
    TaskEvent,
)


def _encode(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _claim_facts(row: ReviewAttempt | None) -> ClaimFacts | None:
    if row is None:
        return None
    return ClaimFacts(
        row.token,
        row.reviewer,
        row.expected_sha,
        row.expires_at,
        row.status,
        row.submitted_payload,
    )


class PostgresReviewWrites:
    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_task(self, task_id: str) -> TaskFacts | None:
        row = self._session.scalar(
            select(Task).where(Task.task_id == task_id).with_for_update()
        )
        if row is None:
            return None
        try:
            status = WorkflowStatus(row.status)
        except ValueError:
            raise CorruptedPersistenceDataError(
                "invalid persisted review task"
            ) from None
        return TaskFacts(
            row.task_id,
            status,
            row.current_head_sha,
            row.base_sha,
            row.completed_review_rounds,
            row.requested_effort,
            bool(row.require_verified_effort),
        )

    def active_claim(self, task_id: str) -> ClaimFacts | None:
        row = self._session.execute(
            select(ReviewAttempt).where(
                ReviewAttempt.task_id == task_id, ReviewAttempt.status == "ACTIVE"
            )
        ).scalar_one_or_none()
        return _claim_facts(row)

    def find_claim(self, task_id: str, token: str) -> ClaimFacts | None:
        row = self._session.scalar(
            select(ReviewAttempt).where(
                ReviewAttempt.task_id == task_id, ReviewAttempt.token == token
            )
        )
        return _claim_facts(row)

    def latest_head_handoff_action(self, task_id: str, sha: str) -> str | None:
        return self._session.scalar(
            select(TaskEvent.action)
            .where(
                TaskEvent.task_id == task_id,
                TaskEvent.sha == sha,
                TaskEvent.action.in_(tuple(TaskEventAction)),
            )
            .order_by(TaskEvent.event_id.desc())
            .limit(1)
        )

    def has_successful_ci(self, task_id: str, sha: str) -> bool:
        statement = select(CITask.task_id).where(
            CITask.task_id == task_id,
            CITask.head_sha == sha,
            CITask.verdict == CiVerdict.SUCCESS,
        )
        try:
            # A failed evidence query must leave the outer transaction usable.
            with self._session.begin_nested():
                return self._session.scalar(statement) is not None
        except DBAPIError:
            return False

    def prior_findings(self, task_id: str) -> set[str]:
        return set(
            self._session.scalars(
                select(ReviewFinding.finding_id)
                .join(Review, ReviewFinding.review_id == Review.review_id)
                .where(Review.task_id == task_id)
            )
        )

    def transition(
        self,
        task: TaskFacts,
        status: WorkflowStatus,
        action: ReviewEventAction,
        now: float,
        metadata: dict[str, str | int],
    ) -> None:
        self._session.add(
            TaskEvent(
                task_id=task.task_id,
                previous_status=task.status,
                next_status=status,
                action=action,
                sha=task.head,
                timestamp=now,
                metadata_text=_encode(metadata),
            )
        )
        self._session.flush()
        self._session.execute(
            update(Task)
            .where(Task.task_id == task.task_id)
            .values(status=status, updated_at=now)
        )

    def expire(self, task: TaskFacts, claim: ClaimFacts, now: float) -> None:
        self._session.execute(
            update(ReviewAttempt)
            .where(
                ReviewAttempt.task_id == task.task_id,
                ReviewAttempt.token == claim.token,
            )
            .values(status="EXPIRED")
        )
        self.transition(
            task,
            WorkflowStatus.READY_IN_REVIEW,
            ReviewEventAction.LEASE_EXPIRED,
            now,
            {},
        )

    def claim(self, task: TaskFacts, claim: ClaimFacts, now: float) -> None:
        self._session.add(
            ReviewAttempt(
                token=claim.token,
                task_id=task.task_id,
                expected_sha=claim.expected_sha,
                reviewer=claim.reviewer,
                claimed_at=now,
                expires_at=claim.expires_at,
                status=claim.status,
            )
        )
        self._session.flush()
        self.transition(
            task, WorkflowStatus.IN_REVIEW, ReviewEventAction.CLAIM, now, {}
        )

    def submit(
        self,
        task: TaskFacts,
        submission: ReviewSubmission,
        canonical: str,
        review_id: str,
        reviewer: str,
        now: float,
        actual: str | None,
        evidence: str,
    ) -> None:
        self._session.add(
            Review(
                review_id=review_id,
                task_id=task.task_id,
                round_number=submission.review_round,
                base_sha=task.base,
                reviewed_head_sha=submission.reviewed_head_sha,
                verdict=submission.verdict,
                summary=submission.summary,
                requested_effort=task.effort,
                actual_effort=actual,
                verification_evidence=evidence,
                reviewer=reviewer,
                reviewed_at=now,
                imported_at=None,
                prior_resolutions=_encode(dict(submission.prior_resolutions)),
            )
        )
        self._session.flush()
        self._session.add_all(
            ReviewFinding(
                review_id=review_id,
                finding_id=finding.finding_id,
                task_id=task.task_id,
                severity=finding.severity,
                acceptance_criterion=finding.acceptance_criterion,
                file_path=finding.file_path,
                line=finding.line,
                description=finding.description,
                suggested_correction=finding.suggested_correction,
                required_verification=finding.required_verification,
            )
            for finding in submission.findings
        )
        self._session.flush()
        self._session.execute(
            update(Task)
            .where(Task.task_id == task.task_id)
            .values(completed_review_rounds=submission.review_round)
        )
        self._session.execute(
            update(ReviewAttempt)
            .where(
                ReviewAttempt.task_id == task.task_id,
                ReviewAttempt.token == submission.claim_token,
            )
            .values(status="SUBMITTED", submitted_payload=canonical)
        )
        self.transition(
            task,
            submission.verdict,
            ReviewEventAction.REVIEW_SUBMITTED,
            now,
            {"review_id": review_id, "round": submission.review_round},
        )
