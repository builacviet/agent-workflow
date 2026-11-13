"""Opt-in PostgreSQL workflow repository; Alembic remains schema authority."""

from contextlib import contextmanager

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import exists, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from agent_workflow.budget import check_deadline, operation_timeout
from agent_workflow.storage.postgres_connection import PostgresFactory
from agent_workflow.storage.postgres_models import (
    CIObservation,
    CITask,
    Review,
    ReviewAttempt,
    ReviewFinding,
    Task,
    TaskEvent,
)
from agent_workflow.storage.repository import WorkflowError


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


class FindingDTO(RecordDTO):
    review_id: str
    finding_id: str
    task_id: str
    severity: str
    acceptance_criterion: str
    file_path: str
    line: int | None
    description: str
    suggested_correction: str
    required_verification: str


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
        # PostgreSQL interprets zero as unlimited; never round a budget down to zero.
        milliseconds = max(1, int(operation_timeout(30) * 1000))
        self.session.connection().execute(
            text(
                "SELECT set_config('lock_timeout', :timeout, true), "
                "set_config('statement_timeout', :timeout, true)"
            ),
            {"timeout": f"{milliseconds}ms"},
        )

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

    def set_status(self, values: tuple = ()) -> None:
        status, updated_at, task_id = values
        self._execute(
            update(Task)
            .where(Task.task_id == task_id)
            .values(status=status, updated_at=updated_at)
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

    def set_rounds(self, values: tuple = ()) -> None:
        rounds, task_id = values
        self._execute(
            update(Task)
            .where(Task.task_id == task_id)
            .values(completed_review_rounds=rounds)
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

    def expire_claim(self, values: tuple = ()) -> None:
        (token,) = values
        self._execute(
            update(ReviewAttempt)
            .where(ReviewAttempt.token == token)
            .values(status="EXPIRED")
        )

    def submit_claim(self, values: tuple = ()) -> None:
        payload, token = values
        self._execute(
            update(ReviewAttempt)
            .where(ReviewAttempt.token == token)
            .values(status="SUBMITTED", submitted_payload=payload)
        )

    def insert_review(self, values: tuple = ()) -> None:
        (
            review_id,
            task_id,
            round_number,
            base,
            head,
            verdict,
            summary,
            requested,
            actual,
            evidence,
            reviewer,
            reviewed,
            imported,
            resolutions,
        ) = values
        self._add(
            Review(
                review_id=review_id,
                task_id=task_id,
                round_number=round_number,
                base_sha=base,
                reviewed_head_sha=head,
                verdict=verdict,
                summary=summary,
                requested_effort=requested,
                actual_effort=actual,
                verification_evidence=evidence,
                reviewer=reviewer,
                reviewed_at=reviewed,
                imported_at=imported,
                prior_resolutions=resolutions,
            )
        )

    def insert_finding(self, values: tuple = ()) -> None:
        (
            review_id,
            finding_id,
            task_id,
            severity,
            criterion,
            path,
            line,
            description,
            correction,
            verification,
        ) = values
        self._add(
            ReviewFinding(
                review_id=review_id,
                finding_id=finding_id,
                task_id=task_id,
                severity=severity,
                acceptance_criterion=criterion,
                file_path=path,
                line=line,
                description=description,
                suggested_correction=correction,
                required_verification=verification,
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

    def list_claimed_tasks(self, values: tuple = ()) -> list[dict]:
        stmt = select(Task).where(Task.status == "IN_REVIEW").order_by(Task.task_id)
        return self._all(stmt.with_for_update() if self.writable else stmt, TaskDTO)

    def list_ready_tasks(self, values: tuple = ()) -> list[dict]:
        return self._all(
            select(Task).where(Task.status == "READY_IN_REVIEW").order_by(Task.task_id),
            TaskDTO,
        )

    def list_reviews(self, values: tuple = ()) -> list[dict]:
        (task_id,) = values
        return self._all(
            select(Review)
            .where(Review.task_id == task_id)
            .order_by(Review.round_number),
            ReviewDTO,
        )

    def list_findings(self, values: tuple = ()) -> list[dict]:
        (review_id,) = values
        return self._all(
            select(ReviewFinding)
            .where(ReviewFinding.review_id == review_id)
            .order_by(ReviewFinding.finding_id),
            FindingDTO,
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

    def advance_failed_ci_head(self, task_id: str, old_head: str, new_head: str) -> int:
        if new_head == old_head:
            raise WorkflowError("CI repair requires a different HEAD")
        failure = exists(
            select(CIObservation.id).where(
                CIObservation.task_id == CITask.task_id,
                CIObservation.pr_number == CITask.pr_number,
                CIObservation.head_sha == CITask.head_sha,
                CIObservation.verdict == CITask.verdict,
                CIObservation.reason == "REQUIRED_CHECK_FAILED",
                CIObservation.check_run_id > 0,
                CIObservation.workflow_run_id > 0,
                CIObservation.run_attempt > 0,
            )
        )
        seen = exists(
            select(CIObservation.id).where(
                CIObservation.task_id == CITask.task_id,
                CIObservation.head_sha == new_head,
            )
        )
        stmt = (
            select(CITask.pr_number)
            .where(
                CITask.task_id == task_id,
                CITask.head_sha == old_head,
                CITask.verdict == "FAILURE",
                CITask.pr_number > 0,
                CITask.repair_heads < 2,
                failure,
                ~seen,
            )
            .with_for_update()
        )
        try:
            with self.session.begin_nested():
                row = self._execute(stmt).first()
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) not in {"42P01", "42703"}:
                raise
            raise WorkflowError("CI repair evidence is unavailable") from exc
        if row is None:
            raise WorkflowError("CI repair HEAD not authorized")
        changed = self._execute(
            update(CITask)
            .where(CITask.task_id == task_id)
            .values(
                head_sha=new_head,
                verdict="BLOCKED",
                repair_heads=CITask.repair_heads + 1,
            )
        ).rowcount
        if changed != 1:
            raise WorkflowError("CI repair update failed")
        return row[0]

    def import_historical_head(self, values: tuple) -> None:
        raise WorkflowError(
            "historical import is unsupported by the PostgreSQL adapter"
        )

    def delete_synthetic_history(self, values: tuple) -> None:
        raise WorkflowError(
            "synthetic history deletion is unsupported by the PostgreSQL adapter"
        )


class PostgresWorkflowRepository:
    def __init__(self, factory: PostgresFactory):
        self.factory = factory

    @contextmanager
    def transaction(self):
        with self.factory.transaction() as connection:
            with Session(bind=connection) as session:
                unit = PostgresWorkflowUnit(session, writable=True)
                unit._timeout()
                yield unit
                check_deadline()
                unit._timeout()
                session.commit()
                check_deadline()

    @contextmanager
    def read(self):
        with self.factory.transaction() as connection:
            connection.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            )
            with Session(bind=connection) as session:
                unit = PostgresWorkflowUnit(session, writable=False)
                unit._timeout()
                yield unit
                check_deadline()
