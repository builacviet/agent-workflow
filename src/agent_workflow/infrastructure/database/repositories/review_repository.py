"""Layer: Infrastructure
Responsibility: Project persisted review history and events in the caller's scope.
Dependencies: Application review DTOs, SQLAlchemy models and the caller's Session.
"""

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_workflow.application.review.dtos import (
    PreviousReview,
    ReviewEvent,
    ReviewFinding,
)
from agent_workflow.domain.states import WorkflowStatus
from agent_workflow.infrastructure.database.models import Review, TaskEvent
from agent_workflow.infrastructure.database.models import (
    ReviewFinding as ReviewFindingRow,
)

_PRIOR_RESOLUTIONS = TypeAdapter(dict[str, str])


class SqlAlchemyReviewRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_reviews(self, task_id: str) -> tuple[PreviousReview, ...]:
        rows = self._session.scalars(
            select(Review)
            .where(Review.task_id == task_id)
            .order_by(Review.round_number)
        ).all()
        if not rows:
            return ()
        findings = self._list_findings(tuple(row.review_id for row in rows))
        return tuple(self._to_review(row, findings[row.review_id]) for row in rows)

    def _to_review(
        self, row: Review, findings: tuple[ReviewFinding, ...]
    ) -> PreviousReview:
        try:
            prior_resolutions = _PRIOR_RESOLUTIONS.validate_json(row.prior_resolutions)
        except ValueError:
            raise ValueError("Invalid persisted review prior resolutions") from None

        return PreviousReview(
            review_id=row.review_id,
            task_id=row.task_id,
            round_number=row.round_number,
            base_sha=row.base_sha,
            reviewed_head_sha=row.reviewed_head_sha,
            verdict=WorkflowStatus(row.verdict),
            summary=row.summary,
            requested_effort=row.requested_effort,
            actual_effort=row.actual_effort,
            verification_evidence=row.verification_evidence,
            reviewer=row.reviewer,
            reviewed_at=row.reviewed_at,
            imported_at=row.imported_at,
            prior_resolutions=tuple(prior_resolutions.items()),
            findings=findings,
        )

    def _list_findings(
        self, review_ids: tuple[str, ...]
    ) -> dict[str, tuple[ReviewFinding, ...]]:
        rows = self._session.scalars(
            select(ReviewFindingRow)
            .where(ReviewFindingRow.review_id.in_(review_ids))
            .order_by(ReviewFindingRow.review_id, ReviewFindingRow.finding_id)
        )
        grouped: dict[str, list[ReviewFinding]] = {
            review_id: [] for review_id in review_ids
        }
        for row in rows:
            grouped[row.review_id].append(
                ReviewFinding(
                    review_id=row.review_id,
                    finding_id=row.finding_id,
                    task_id=row.task_id,
                    severity=row.severity,
                    acceptance_criterion=row.acceptance_criterion,
                    file_path=row.file_path,
                    line=row.line,
                    description=row.description,
                    suggested_correction=row.suggested_correction,
                    required_verification=row.required_verification,
                )
            )
        return {review_id: tuple(findings) for review_id, findings in grouped.items()}

    def list_events(self, task_id: str) -> tuple[ReviewEvent, ...]:
        rows = self._session.scalars(
            select(TaskEvent)
            .where(TaskEvent.task_id == task_id)
            .order_by(TaskEvent.event_id)
        )
        return tuple(
            ReviewEvent(
                event_id=row.event_id,
                task_id=row.task_id,
                previous_status=(
                    WorkflowStatus(row.previous_status)
                    if row.previous_status is not None
                    else None
                ),
                next_status=WorkflowStatus(row.next_status),
                action=row.action,
                sha=row.sha,
                timestamp=row.timestamp,
                metadata=row.metadata_text,
            )
            for row in rows
        )
