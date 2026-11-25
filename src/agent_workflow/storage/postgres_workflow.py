"""Opt-in PostgreSQL workflow repository; Alembic remains schema authority."""

from contextlib import contextmanager

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from agent_workflow.budget import check_deadline
from agent_workflow.domain.exceptions import BusinessRuleError
from agent_workflow.infrastructure.database.models import (
    CITask,
    Review,
    ReviewAttempt,
    Task,
    TaskEvent,
)
from agent_workflow.infrastructure.database.session_factory import PostgresFactory
from agent_workflow.infrastructure.database.unit_of_work import (
    set_workflow_query_timeout,
)


class RecordDTO(BaseModel):
    """Explicit, shallow conversion to the existing dictionary contract."""

    model_config = ConfigDict(from_attributes=True)


class TaskDTO(RecordDTO):
    task_id: str
    metadata: str = Field(validation_alias="metadata_text")
    repository: str
    branch: str
    base_sha: str
    current_head_sha: str | None
    requested_effort: str
    require_verified_effort: int
    max_review_rounds: int
    completed_review_rounds: int
    status: str
    created_at: float
    updated_at: float


class AttemptDTO(RecordDTO):
    token: str
    task_id: str
    expected_sha: str
    reviewer: str
    claimed_at: float
    expires_at: float
    status: str
    submitted_payload: str | None


class ReviewDTO(RecordDTO):
    review_id: str
    task_id: str
    round_number: int
    base_sha: str
    reviewed_head_sha: str
    verdict: str
    summary: str
    requested_effort: str
    actual_effort: str | None
    verification_evidence: str
    reviewer: str | None
    reviewed_at: float | None
    imported_at: float | None
    prior_resolutions: str


class EventDTO(RecordDTO):
    event_id: int
    task_id: str
    previous_status: str | None
    next_status: str
    action: str
    sha: str | None
    timestamp: float
    metadata: str = Field(validation_alias="metadata_text")


class PostgresWorkflowUnit:
    def __init__(self, session: Session, *, writable: bool):
        self.session = session
        self.writable = writable

    def _timeout(self) -> None:
        set_workflow_query_timeout(self.session)

    def _execute(self, statement):
        self._timeout()
        return self.session.execute(statement)

    def _add(self, entity) -> None:
        self._timeout()
        self.session.add(entity)
        self.session.flush()

    def _one(self, statement, dto):
        entity = self._execute(statement).scalar_one_or_none()
        return None if entity is None else dto.model_validate(entity).model_dump()

    def _all(self, statement, dto):
        return [
            dto.model_validate(row).model_dump()
            for row in self._execute(statement).scalars()
        ]

    def insert_event(self, values: tuple = ()) -> None:
        task_id, previous, next_status, action, sha, timestamp, metadata = values
        self._add(
            TaskEvent(
                task_id=task_id,
                previous_status=previous,
                next_status=next_status,
                action=action,
                sha=sha,
                timestamp=timestamp,
                metadata_text=metadata,
            )
        )

    def insert_task(self, values: tuple = ()) -> None:
        (
            task_id,
            metadata,
            repository,
            branch,
            base_sha,
            effort,
            verified,
            status,
            created,
            updated,
        ) = values
        self._add(
            Task(
                task_id=task_id,
                metadata_text=metadata,
                repository=repository,
                branch=branch,
                base_sha=base_sha,
                requested_effort=effort,
                require_verified_effort=verified,
                status=status,
                created_at=created,
                updated_at=updated,
            )
        )

    def set_head(self, values: tuple = ()) -> None:
        head, task_id = values
        self._execute(
            update(Task).where(Task.task_id == task_id).values(current_head_sha=head)
        )

    def insert_claim(self, values: tuple = ()) -> None:
        token, task_id, sha, reviewer, claimed, expires = values
        self._add(
            ReviewAttempt(
                token=token,
                task_id=task_id,
                expected_sha=sha,
                reviewer=reviewer,
                claimed_at=claimed,
                expires_at=expires,
                status="ACTIVE",
            )
        )

    def find_task(self, values: tuple = ()) -> dict | None:
        (task_id,) = values
        stmt = select(Task).where(Task.task_id == task_id)
        return self._one(stmt.with_for_update() if self.writable else stmt, TaskDTO)

    def find_authorization(self, values: tuple = ()) -> dict | None:
        (task_id,) = values
        if self.writable:
            self._timeout()
            self.session.execute(
                text(
                    "SELECT pg_advisory_xact_lock(hashtextextended(:task_id, 5727811))"
                ),
                {"task_id": task_id},
            )
        result = self._execute(
            select(Task.metadata_text).where(Task.task_id == task_id)
        ).first()
        return {"metadata": result[0]} if result else None

    def find_active_claim(self, values: tuple = ()) -> dict | None:
        (task_id,) = values
        return self._one(
            select(ReviewAttempt).where(
                ReviewAttempt.task_id == task_id, ReviewAttempt.status == "ACTIVE"
            ),
            AttemptDTO,
        )

    def find_claim(self, values: tuple = ()) -> dict | None:
        token, task_id = values
        return self._one(
            select(ReviewAttempt).where(
                ReviewAttempt.token == token, ReviewAttempt.task_id == task_id
            ),
            AttemptDTO,
        )

    def list_reviews(self, values: tuple = ()) -> list[dict]:
        (task_id,) = values
        return self._all(
            select(Review)
            .where(Review.task_id == task_id)
            .order_by(Review.round_number),
            ReviewDTO,
        )

    def list_events(self, values: tuple = ()) -> list[dict]:
        (task_id,) = values
        return self._all(
            select(TaskEvent)
            .where(TaskEvent.task_id == task_id)
            .order_by(TaskEvent.event_id),
            EventDTO,
        )

    def find_head_handoff(self, task_id: str, head_sha: str | None) -> dict | None:
        # SQLite's equality comparison with NULL never matches a handoff.
        if head_sha is None:
            return None
        row = self._execute(
            select(TaskEvent.action, TaskEvent.metadata_text)
            .where(
                TaskEvent.task_id == task_id,
                TaskEvent.sha == head_sha,
                TaskEvent.action.in_(["HANDOFF", "CI_REPAIR_HANDOFF"]),
            )
            .order_by(TaskEvent.event_id.desc())
            .limit(1)
        ).first()
        return {"action": row[0], "metadata": row[1]} if row else None

    def ci_approved(self, values: tuple = ()) -> bool:
        task_id, head = values
        try:
            with self.session.begin_nested():
                return (
                    self._execute(
                        select(CITask.task_id)
                        .where(
                            CITask.task_id == task_id,
                            CITask.head_sha == head,
                            CITask.verdict == "SUCCESS",
                        )
                        .limit(1)
                    ).first()
                    is not None
                )
        except DBAPIError:
            return False

    def import_historical_head(self, values: tuple) -> None:
        raise BusinessRuleError(
            "historical import is unsupported by the PostgreSQL adapter"
        )

    def delete_synthetic_history(self, values: tuple) -> None:
        raise BusinessRuleError(
            "synthetic history deletion is unsupported by the PostgreSQL adapter"
        )


class PostgresWorkflowRepository:
    def __init__(self, factory: PostgresFactory):
        self.factory = factory

    @contextmanager
    def transaction(self):
        with self.factory.unit_of_work_session(
            after_session_commit=check_deadline
        ) as session:
            unit = PostgresWorkflowUnit(session, writable=True)
            unit._timeout()
            yield unit
            check_deadline()
            unit._timeout()

    @contextmanager
    def read(self):
        with self.factory.unit_of_work_session(read_only=True) as session:
            unit = PostgresWorkflowUnit(session, writable=False)
            unit._timeout()
            yield unit
            check_deadline()
