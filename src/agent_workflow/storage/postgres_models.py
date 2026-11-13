"""Typed mappings for the existing Alembic baseline; never creates schema."""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class WebhookInboxRow(Base):
    __tablename__ = "webhook_inbox"
    __table_args__ = (Index("webhook_inbox_body_digest", "body_digest", unique=True),)

    delivery_id: Mapped[str] = mapped_column(Text, primary_key=True)
    event: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    repository: Mapped[str] = mapped_column(Text, nullable=False)
    installation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    subject_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    head_sha: Mapped[str] = mapped_column(Text, nullable=False)
    body_digest: Mapped[str] = mapped_column(Text, nullable=False)


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("max_review_rounds = 2", name="max_rounds_two"),
        CheckConstraint(
            "completed_review_rounds BETWEEN 0 AND 2", name="completed_rounds_range"
        ),
        CheckConstraint(
            "status IN ('AWAITING_HANDOFF','READY_IN_REVIEW','IN_REVIEW','CHANGES_REQUESTED','APPROVED','ESCALATED')",
            name="task_status_set",
        ),
    )
    task_id: Mapped[str] = mapped_column(Text, primary_key=True)
    metadata_text: Mapped[str] = mapped_column("metadata", Text, nullable=False)
    repository: Mapped[str] = mapped_column(Text, nullable=False)
    branch: Mapped[str] = mapped_column(Text, nullable=False)
    base_sha: Mapped[str] = mapped_column(Text, nullable=False)
    current_head_sha: Mapped[str | None] = mapped_column(Text)
    requested_effort: Mapped[str] = mapped_column(Text, nullable=False)
    require_verified_effort: Mapped[int] = mapped_column(Integer, nullable=False)
    max_review_rounds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="2"
    )
    completed_review_rounds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[float] = mapped_column(Float(precision=53), nullable=False)
    updated_at: Mapped[float] = mapped_column(Float(precision=53), nullable=False)
    attempts: Mapped[list["ReviewAttempt"]] = relationship(back_populates="task")
    reviews: Mapped[list["Review"]] = relationship(back_populates="task")
    events: Mapped[list["TaskEvent"]] = relationship(back_populates="task")


class ReviewAttempt(Base):
    __tablename__ = "review_attempts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE','EXPIRED','SUBMITTED')",
            name="review_attempt_status_set",
        ),
        Index(
            "one_active_claim",
            "task_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )
    token: Mapped[str] = mapped_column(Text, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.task_id"), nullable=False)
    expected_sha: Mapped[str] = mapped_column(Text, nullable=False)
    reviewer: Mapped[str] = mapped_column(Text, nullable=False)
    claimed_at: Mapped[float] = mapped_column(Float(precision=53), nullable=False)
    expires_at: Mapped[float] = mapped_column(Float(precision=53), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_payload: Mapped[str | None] = mapped_column(Text)
    task: Mapped[Task] = relationship(back_populates="attempts")


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("task_id", "round_number", name="one_review_per_round"),
        UniqueConstraint("task_id", "reviewed_head_sha", name="one_review_per_head"),
        CheckConstraint("round_number BETWEEN 1 AND 2", name="review_round_range"),
    )
    review_id: Mapped[str] = mapped_column(Text, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.task_id"), nullable=False)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    base_sha: Mapped[str] = mapped_column(Text, nullable=False)
    reviewed_head_sha: Mapped[str] = mapped_column(Text, nullable=False)
    verdict: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    requested_effort: Mapped[str] = mapped_column(Text, nullable=False)
    actual_effort: Mapped[str | None] = mapped_column(Text)
    verification_evidence: Mapped[str] = mapped_column(Text, nullable=False)
    reviewer: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[float | None] = mapped_column(Float(precision=53))
    imported_at: Mapped[float | None] = mapped_column(Float(precision=53))
    prior_resolutions: Mapped[str] = mapped_column(Text, nullable=False)
    task: Mapped[Task] = relationship(back_populates="reviews")
    findings: Mapped[list["ReviewFinding"]] = relationship(back_populates="review")


class ReviewFinding(Base):
    __tablename__ = "review_findings"
    review_id: Mapped[str] = mapped_column(
        ForeignKey("reviews.review_id"), nullable=False
    )
    finding_id: Mapped[str] = mapped_column(Text, primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.task_id"), primary_key=True)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    acceptance_criterion: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    line: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_correction: Mapped[str] = mapped_column(Text, nullable=False)
    required_verification: Mapped[str] = mapped_column(Text, nullable=False)
    review: Mapped[Review] = relationship(back_populates="findings")


class TaskEvent(Base):
    __tablename__ = "task_events"
    event_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.task_id"), nullable=False)
    previous_status: Mapped[str | None] = mapped_column(Text)
    next_status: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    sha: Mapped[str | None] = mapped_column(Text)
    timestamp: Mapped[float] = mapped_column(Float(precision=53), nullable=False)
    metadata_text: Mapped[str] = mapped_column("metadata", Text, nullable=False)
    task: Mapped[Task] = relationship(back_populates="events")


class CITask(Base):
    __tablename__ = "controller_ci_tasks"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.task_id"), primary_key=True)
    pr_number: Mapped[int] = mapped_column(Integer, nullable=False)
    head_sha: Mapped[str] = mapped_column(Text, nullable=False)
    verdict: Mapped[str] = mapped_column(Text, nullable=False)
    repair_heads: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )


class CIObservation(Base):
    __tablename__ = "controller_ci_observations"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("controller_ci_tasks.task_id"), nullable=False
    )
    pr_number: Mapped[int] = mapped_column(Integer, nullable=False)
    head_sha: Mapped[str] = mapped_column(Text, nullable=False)
    verdict: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    check_run_id: Mapped[int | None] = mapped_column(BigInteger)
    observed_at: Mapped[str] = mapped_column(Text, nullable=False)
    workflow_run_id: Mapped[int | None] = mapped_column(BigInteger)
    run_attempt: Mapped[int | None] = mapped_column(Integer)


class CIProcessedDelivery(Base):
    __tablename__ = "controller_ci_processed"

    delivery_id: Mapped[str] = mapped_column(
        ForeignKey("webhook_inbox.delivery_id"), primary_key=True
    )
    processed_at: Mapped[float] = mapped_column(
        Float(precision=53),
        nullable=False,
        server_default=text("EXTRACT(EPOCH FROM CURRENT_TIMESTAMP)"),
    )


class AgentDispatch(Base):
    __tablename__ = "agent_dispatches"
    __table_args__ = (
        UniqueConstraint("slot", name="only_one_global_dispatch_slot"),
        UniqueConstraint(
            "task_id", "round_number", "input_sha", name="dispatch_replay_guard"
        ),
        UniqueConstraint(
            "role",
            "phase",
            "subject_type",
            "subject_id",
            "subject_version",
            name="agent_dispatch_identity",
        ),
        CheckConstraint("slot = 1", name="dispatch_slot_one"),
        CheckConstraint(
            "status IN ('ACTIVE','BLOCKED','DONE','ACKNOWLEDGED')",
            name="dispatch_status_set",
        ),
        CheckConstraint(
            "(status IN ('ACTIVE','BLOCKED') AND slot IS NOT NULL AND slot = 1) OR (status IN ('DONE','ACKNOWLEDGED') AND slot IS NULL)",
            name="agent_slot_state",
        ),
        CheckConstraint(
            "role <> 'dev' OR phase <> 'code' OR (task_id IS NOT NULL AND round_number IS NOT NULL AND input_sha IS NOT NULL AND subject_type = 'task' AND subject_id = task_id AND subject_version = round_number::text || ':' || input_sha)",
            name="agent_dev_identity",
        ),
    )
    dispatch_id: Mapped[str] = mapped_column(Text, primary_key=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    phase: Mapped[str] = mapped_column(Text, nullable=False)
    subject_type: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[str] = mapped_column(Text, nullable=False)
    subject_version: Mapped[str] = mapped_column(Text, nullable=False)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.task_id"))
    round_number: Mapped[int | None] = mapped_column(Integer)
    input_sha: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    slot: Mapped[int | None] = mapped_column(Integer)
    deadline: Mapped[float] = mapped_column(Float(precision=53), nullable=False)
    result_sha: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[str] = mapped_column(Text, nullable=False, server_default="")


class AgentExecutionEvidence(Base):
    __tablename__ = "agent_execution_evidence"
    __table_args__ = (
        CheckConstraint(
            "evidence_kind <> 'dev_runtime' OR (task_id IS NOT NULL AND input_sha IS NOT NULL AND runtime_name IS NOT NULL AND declared_runtime_version IS NOT NULL AND inference_telemetry IS NOT NULL AND tested_digest IS NOT NULL AND published_head_sha IS NOT NULL)",
            name="agent_dev_evidence_fields",
        ),
    )
    dispatch_id: Mapped[str] = mapped_column(
        ForeignKey("agent_dispatches.dispatch_id"), primary_key=True
    )
    evidence_kind: Mapped[str] = mapped_column(Text, nullable=False)
    trusted_writer: Mapped[str] = mapped_column(Text, nullable=False)
    task_id: Mapped[str | None] = mapped_column(Text)
    input_sha: Mapped[str | None] = mapped_column(Text)
    runtime_name: Mapped[str | None] = mapped_column(Text)
    declared_runtime_version: Mapped[str | None] = mapped_column(Text)
    observed_image_id: Mapped[str | None] = mapped_column(Text)
    inference_telemetry: Mapped[str | None] = mapped_column(Text)
    tested_digest: Mapped[str | None] = mapped_column(Text)
    published_head_sha: Mapped[str | None] = mapped_column(Text)
    recorded_at: Mapped[float] = mapped_column(Float(precision=53), nullable=False)
