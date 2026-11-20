"""PostgreSQL adapter for explicitly authorized local fixtures."""

import json
import time

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from agent_workflow.domain.task.authorization import (
    TaskAuthorization,
    TaskAuthorizationSpec,
)
from agent_workflow.domain.workflow_identity import WorkflowError
from agent_workflow.infrastructure.database.models import (
    AgentDispatch,
    AgentExecutionEvidence,
    CITask,
    Review,
    ReviewAttempt,
    ReviewFinding,
    Task,
    TaskEvent,
)
from agent_workflow.infrastructure.database.repositories.task_authorization_repository import (
    SqlAlchemyTaskAuthorizationRepository,
)


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class SqlAlchemyAdminSeedRepository:
    """Operate on fixed task identities inside the caller's transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._authorization = SqlAlchemyTaskAuthorizationRepository(session)

    def _load(self, spec: TaskAuthorizationSpec) -> Task | None:
        existing = self._authorization.load_for_authorization(spec.task_id)
        if existing is None:
            return None
        spec.match_existing(existing)
        return self._session.get(Task, spec.task_id)

    def _create(self, spec: TaskAuthorizationSpec) -> Task:
        now = time.time()
        self._authorization.add(TaskAuthorization(spec, now, now))
        task = self._session.get(Task, spec.task_id)
        assert task is not None
        return task

    def reset_synthetic(self, spec: TaskAuthorizationSpec) -> None:
        if spec.task_id != "POC-REVIEW-001" or spec.synthetic is not True:
            raise WorkflowError("refusing to reset real history")
        task = self._load(spec)
        if task is None:
            raise WorkflowError("synthetic task does not exist")
        try:
            metadata = json.loads(task.metadata_text)
        except (TypeError, ValueError):
            raise WorkflowError("invalid synthetic task metadata") from None
        if type(metadata) is not dict or metadata.get("synthetic") is not True:
            raise WorkflowError("refusing to reset real history")
        session = self._session
        task_id = spec.task_id
        # External workflow records are never removed by a fixture reset.
        checks = (
            select(CITask.task_id).where(CITask.task_id == task_id),
            select(AgentDispatch.dispatch_id).where(AgentDispatch.task_id == task_id),
            select(AgentExecutionEvidence.dispatch_id).where(
                AgentExecutionEvidence.task_id == task_id
            ),
            select(ReviewFinding.finding_id)
            .join(Review, ReviewFinding.review_id == Review.review_id)
            .where(Review.task_id == task_id, ReviewFinding.task_id != task_id),
            select(ReviewFinding.finding_id)
            .join(Review, ReviewFinding.review_id == Review.review_id)
            .where(ReviewFinding.task_id == task_id, Review.task_id != task_id),
        )
        if any(session.scalar(query.limit(1)) is not None for query in checks):
            raise WorkflowError("synthetic task has linked external history")
        for model in (ReviewFinding, Review, ReviewAttempt, TaskEvent, Task):
            session.execute(delete(model).where(model.task_id == task_id))
        session.flush()

    def seed_synthetic(self, spec: TaskAuthorizationSpec) -> None:
        if spec.task_id != "POC-REVIEW-001" or spec.synthetic is not True:
            raise WorkflowError("invalid synthetic fixture")
        task = self._load(spec)
        if task is None:
            task = self._create(spec)
        if task.status != "AWAITING_HANDOFF":
            return
        if task.current_head_sha is not None or task.completed_review_rounds != 0:
            raise WorkflowError("synthetic handoff conflicts with task state")
        if "docs/handoff.md" not in spec.context_paths:
            raise WorkflowError("handoff path is not authorized")
        if self._session.scalar(
            select(Review.review_id).where(Review.task_id == spec.task_id).limit(1)
        ):
            raise WorkflowError("synthetic handoff conflicts with review history")
        now = time.time()
        task.current_head_sha = spec.base_sha
        task.status = "READY_IN_REVIEW"
        task.updated_at = now
        self._session.add(
            TaskEvent(
                task_id=spec.task_id,
                previous_status="AWAITING_HANDOFF",
                next_status="READY_IN_REVIEW",
                action="HANDOFF",
                sha=spec.base_sha,
                timestamp=now,
                metadata_text=_json(
                    {
                        "handoff_file_path": "docs/handoff.md",
                        "test_summary": "SYNTHETIC fixture registration only; no test execution claimed by this seed.",
                        "verification": "reporter_supplied_not_remotely_verified",
                    }
                ),
            )
        )
        self._session.flush()

    def import_history(self, spec: TaskAuthorizationSpec) -> None:
        if spec.task_id != "TASK-001" or spec.synthetic is not False:
            raise WorkflowError("invalid historical fixture")
        records = (
            (
                1,
                "6352c8ff5ea83450a93cd1077d6447170c9dedaa",
                "CHANGES_REQUESTED",
                {},
                "F-001",
                "AC3",
                "Dynamic subclass dispatch could bypass domain validation.",
                "Call the base domain validator directly.",
            ),
            (
                2,
                "ff1458fb98dec3a321ca9b96ed8723e40ba882a9",
                "ESCALATED",
                {"F-001": "FIXED"},
                "F-002",
                "AC2",
                "Subclass-defined equality can bypass request conflict detection.",
                "Not provided in historical evidence.",
            ),
        )
        task = self._load(spec)
        if task is not None:
            self._check_history(task, records, spec)
            return
        task = self._create(spec)
        imported = time.time()
        previous = "AWAITING_HANDOFF"
        for (
            number,
            sha,
            verdict,
            resolutions,
            finding_id,
            ac,
            description,
            fix,
        ) in records:
            review_id = f"historical-TASK-001-{number}"
            self._session.add(
                Review(
                    review_id=review_id,
                    task_id=spec.task_id,
                    round_number=number,
                    base_sha=spec.base_sha,
                    reviewed_head_sha=sha,
                    verdict=verdict,
                    summary=description,
                    requested_effort="high",
                    actual_effort=None,
                    verification_evidence="unverified",
                    reviewer=None,
                    reviewed_at=None,
                    imported_at=imported,
                    prior_resolutions=_json(resolutions),
                )
            )
            self._session.add(
                ReviewFinding(
                    review_id=review_id,
                    finding_id=finding_id,
                    task_id=spec.task_id,
                    severity="medium",
                    acceptance_criterion=ac,
                    file_path="src/order_service/service.py",
                    line=None,
                    description=description,
                    suggested_correction=fix,
                    required_verification="Not provided in historical evidence.",
                )
            )
            now = time.time()
            self._session.add(
                TaskEvent(
                    task_id=spec.task_id,
                    previous_status=previous,
                    next_status=verdict,
                    action="HISTORICAL_IMPORT",
                    sha=sha,
                    timestamp=now,
                    metadata_text=_json(
                        {
                            "round": number,
                            "original_timestamp": None,
                            "original_reviewer": None,
                        }
                    ),
                )
            )
            previous = verdict
            task.current_head_sha = sha
            task.completed_review_rounds = number
            task.status = verdict
            task.updated_at = now
        self._session.flush()

    def _check_history(
        self, task: Task, records: tuple, spec: TaskAuthorizationSpec
    ) -> None:
        session = self._session
        reviews = list(
            session.scalars(
                select(Review)
                .where(Review.task_id == spec.task_id)
                .order_by(Review.round_number)
            )
        )
        findings = list(
            session.scalars(
                select(ReviewFinding)
                .where(ReviewFinding.task_id == spec.task_id)
                .order_by(ReviewFinding.finding_id)
            )
        )
        events = list(
            session.scalars(
                select(TaskEvent)
                .where(TaskEvent.task_id == spec.task_id)
                .order_by(TaskEvent.event_id)
            )
        )
        if (
            (task.status, task.completed_review_rounds, task.current_head_sha)
            != ("ESCALATED", 2, records[-1][1])
            or len(reviews) != 2
            or len(findings) != 2
            or len(events) != 3
        ):
            raise WorkflowError("refusing to overwrite divergent task history")
        if (
            session.scalar(
                select(ReviewAttempt.token)
                .where(ReviewAttempt.task_id == spec.task_id)
                .limit(1)
            )
            is not None
        ):
            raise WorkflowError("refusing to overwrite divergent task history")
        expected_events = [
            (None, "AWAITING_HANDOFF", "LOCAL_AUTHORIZATION", None, {}),
            (
                "AWAITING_HANDOFF",
                "CHANGES_REQUESTED",
                "HISTORICAL_IMPORT",
                records[0][1],
                {"round": 1, "original_timestamp": None, "original_reviewer": None},
            ),
            (
                "CHANGES_REQUESTED",
                "ESCALATED",
                "HISTORICAL_IMPORT",
                records[1][1],
                {"round": 2, "original_timestamp": None, "original_reviewer": None},
            ),
        ]
        try:
            actual_events = [
                (
                    e.previous_status,
                    e.next_status,
                    e.action,
                    e.sha,
                    json.loads(e.metadata_text),
                )
                for e in events
            ]
        except (TypeError, ValueError):
            raise WorkflowError(
                "refusing to overwrite divergent task history"
            ) from None
        if actual_events != expected_events:
            raise WorkflowError("refusing to overwrite divergent task history")
        for review, finding, record in zip(reviews, findings, records, strict=True):
            number, sha, verdict, resolutions, finding_id, ac, description, fix = record
            expected_review = (
                f"historical-TASK-001-{number}",
                spec.base_sha,
                sha,
                verdict,
                description,
                "high",
                None,
                "unverified",
                None,
                None,
                _json(resolutions),
            )
            actual_review = (
                review.review_id,
                review.base_sha,
                review.reviewed_head_sha,
                review.verdict,
                review.summary,
                review.requested_effort,
                review.actual_effort,
                review.verification_evidence,
                review.reviewer,
                review.reviewed_at,
                review.prior_resolutions,
            )
            expected_finding = (
                review.review_id,
                finding_id,
                "medium",
                ac,
                "src/order_service/service.py",
                None,
                description,
                fix,
                "Not provided in historical evidence.",
            )
            actual_finding = (
                finding.review_id,
                finding.finding_id,
                finding.severity,
                finding.acceptance_criterion,
                finding.file_path,
                finding.line,
                finding.description,
                finding.suggested_correction,
                finding.required_verification,
            )
            if (
                actual_review != expected_review
                or actual_finding != expected_finding
                or review.imported_at is None
            ):
                raise WorkflowError("refusing to overwrite divergent task history")
