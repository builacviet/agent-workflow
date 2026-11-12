"""Fresh PostgreSQL schema for V1. No SQLite upgrade or runtime cutover."""
from alembic import op
import sqlalchemy as sa

revision = "pg01_baseline"
down_revision = None
branch_labels = None
depends_on = None
SCHEMA = "wfc"


def create(name: str, *columns) -> None:
    op.create_table(name, *columns, schema=SCHEMA)


def upgrade() -> None:
    # Fail closed if an unversioned or unexpected wfc schema already exists.
    op.execute("CREATE SCHEMA wfc")
    create(
        "tasks",
        sa.Column("task_id", sa.Text(), primary_key=True),
        sa.Column("metadata", sa.Text(), nullable=False),
        sa.Column("repository", sa.Text(), nullable=False),
        sa.Column("branch", sa.Text(), nullable=False),
        sa.Column("base_sha", sa.Text(), nullable=False),
        sa.Column("current_head_sha", sa.Text()),
        sa.Column("requested_effort", sa.Text(), nullable=False),
        sa.Column("require_verified_effort", sa.Integer(), nullable=False),
        sa.Column("max_review_rounds", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("completed_review_rounds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Float(precision=53), nullable=False),
        sa.Column("updated_at", sa.Float(precision=53), nullable=False),
        sa.CheckConstraint("max_review_rounds = 2", name="max_rounds_two"),
        sa.CheckConstraint("completed_review_rounds BETWEEN 0 AND 2", name="completed_rounds_range"),
        sa.CheckConstraint(
            "status IN ('AWAITING_HANDOFF','READY_IN_REVIEW','IN_REVIEW',"
            "'CHANGES_REQUESTED','APPROVED','ESCALATED')",
            name="task_status_set",
        ),
    )
    create(
        "review_attempts",
        sa.Column("token", sa.Text(), primary_key=True),
        sa.Column("task_id", sa.Text(), sa.ForeignKey("wfc.tasks.task_id"), nullable=False),
        sa.Column("expected_sha", sa.Text(), nullable=False),
        sa.Column("reviewer", sa.Text(), nullable=False),
        sa.Column("claimed_at", sa.Float(precision=53), nullable=False),
        sa.Column("expires_at", sa.Float(precision=53), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("submitted_payload", sa.Text()),
        sa.CheckConstraint("status IN ('ACTIVE','EXPIRED','SUBMITTED')", name="review_attempt_status_set"),
    )
    op.create_index(
        "one_active_claim", "review_attempts", ["task_id"],
        unique=True, schema=SCHEMA, postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    create(
        "reviews",
        sa.Column("review_id", sa.Text(), primary_key=True),
        sa.Column("task_id", sa.Text(), sa.ForeignKey("wfc.tasks.task_id"), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("base_sha", sa.Text(), nullable=False),
        sa.Column("reviewed_head_sha", sa.Text(), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("requested_effort", sa.Text(), nullable=False),
        sa.Column("actual_effort", sa.Text()),
        sa.Column("verification_evidence", sa.Text(), nullable=False),
        sa.Column("reviewer", sa.Text()),
        sa.Column("reviewed_at", sa.Float(precision=53)),
        sa.Column("imported_at", sa.Float(precision=53)),
        sa.Column("prior_resolutions", sa.Text(), nullable=False),
        sa.UniqueConstraint("task_id", "round_number", name="one_review_per_round"),
        sa.UniqueConstraint("task_id", "reviewed_head_sha", name="one_review_per_head"),
        sa.CheckConstraint("round_number BETWEEN 1 AND 2", name="review_round_range"),
    )
    create(
        "review_findings",
        sa.Column("review_id", sa.Text(), sa.ForeignKey("wfc.reviews.review_id"), nullable=False),
        sa.Column("finding_id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), sa.ForeignKey("wfc.tasks.task_id"), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("acceptance_criterion", sa.Text(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("line", sa.Integer()),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("suggested_correction", sa.Text(), nullable=False),
        sa.Column("required_verification", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("task_id", "finding_id"),
    )
    create(
        "task_events",
        sa.Column("event_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("task_id", sa.Text(), sa.ForeignKey("wfc.tasks.task_id"), nullable=False),
        sa.Column("previous_status", sa.Text()),
        sa.Column("next_status", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("sha", sa.Text()),
        sa.Column("timestamp", sa.Float(precision=53), nullable=False),
        sa.Column("metadata", sa.Text(), nullable=False),
    )
    create(
        "coder_dispatches",
        sa.Column("dispatch_id", sa.Text(), primary_key=True),
        sa.Column("task_id", sa.Text(), sa.ForeignKey("wfc.tasks.task_id"), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("input_sha", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("slot", sa.Integer()),
        sa.Column("deadline", sa.Float(precision=53), nullable=False),
        sa.Column("result_sha", sa.Text()),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.UniqueConstraint("slot", name="only_one_global_dispatch_slot"),
        sa.UniqueConstraint("task_id", "round_number", "input_sha", name="dispatch_replay_guard"),
        sa.CheckConstraint("slot = 1", name="dispatch_slot_one"),
        sa.CheckConstraint("status IN ('ACTIVE','BLOCKED','DONE','ACKNOWLEDGED')", name="dispatch_status_set"),
    )
    create(
        "coder_runtime_evidence",
        sa.Column(
            "dispatch_id", sa.Text(),
            sa.ForeignKey("wfc.coder_dispatches.dispatch_id"), primary_key=True,
        ),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("input_sha", sa.Text(), nullable=False),
        sa.Column("runtime_name", sa.Text(), nullable=False),
        sa.Column("declared_runtime_version", sa.Text(), nullable=False),
        sa.Column("observed_image_id", sa.Text()),
        sa.Column("inference_telemetry", sa.Text(), nullable=False),
        sa.Column("tested_digest", sa.Text(), nullable=False),
        sa.Column("published_head_sha", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.Float(precision=53), nullable=False),
    )
    create(
        "webhook_inbox",
        sa.Column("delivery_id", sa.Text(), primary_key=True),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("repository", sa.Text(), nullable=False),
        sa.Column("installation_id", sa.BigInteger(), nullable=False),
        sa.Column("subject_id", sa.BigInteger(), nullable=False),
        sa.Column("head_sha", sa.Text(), nullable=False),
        sa.Column("body_digest", sa.Text(), nullable=False),
    )
    op.create_index("webhook_inbox_body_digest", "webhook_inbox", ["body_digest"], schema=SCHEMA)
    create(
        "controller_ci_tasks",
        sa.Column("task_id", sa.Text(), sa.ForeignKey("wfc.tasks.task_id"), primary_key=True),
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("head_sha", sa.Text(), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("repair_heads", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("pr_number > 0", name="ci_pr_positive"),
        sa.CheckConstraint("verdict IN ('BLOCKED','FAILURE','SUCCESS')", name="ci_task_verdict_set"),
        sa.CheckConstraint("repair_heads BETWEEN 0 AND 2", name="ci_repair_bounds"),
    )
    create(
        "controller_ci_observations",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("task_id", sa.Text(), sa.ForeignKey("wfc.controller_ci_tasks.task_id"), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("head_sha", sa.Text(), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("check_run_id", sa.BigInteger()),
        sa.Column("observed_at", sa.Text(), nullable=False),
        sa.Column("workflow_run_id", sa.BigInteger()),
        sa.Column("run_attempt", sa.Integer()),
        sa.CheckConstraint("verdict IN ('BLOCKED','FAILURE','SUCCESS')", name="ci_observation_verdict_set"),
    )
    op.create_index(
        "controller_ci_observation_lookup", "controller_ci_observations",
        ["task_id", "head_sha", "verdict"], schema=SCHEMA,
    )
    create(
        "controller_ci_processed",
        sa.Column(
            "delivery_id", sa.Text(),
            sa.ForeignKey("wfc.webhook_inbox.delivery_id"), primary_key=True,
        ),
        sa.Column(
            "processed_at", sa.Float(precision=53), nullable=False,
            server_default=sa.text("EXTRACT(EPOCH FROM CURRENT_TIMESTAMP)"),
        ),
    )
    # Only the table exists: R1-04B1 SQLite implementation is still Draft and
    # the future PostgreSQL adapter must be separately reviewed/approved.
    create(
        "publication_intents",
        sa.Column(
            "dispatch_id", sa.Text(),
            sa.ForeignKey("wfc.coder_dispatches.dispatch_id"), primary_key=True,
        ),
        sa.Column("task_id", sa.Text(), sa.ForeignKey("wfc.tasks.task_id"), nullable=False),
        sa.Column("repository", sa.Text(), nullable=False),
        sa.Column("branch", sa.Text(), nullable=False),
        sa.Column("base_sha", sa.Text(), nullable=False),
        sa.Column("input_sha", sa.Text(), nullable=False),
        sa.Column("expected_head_sha", sa.Text(), nullable=False),
        sa.Column("tested_digest", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("pr_number", sa.Integer()),
        sa.Column("pr_url", sa.Text()),
        sa.Column("created_at", sa.Float(precision=53), nullable=False),
        sa.Column("verified_at", sa.Float(precision=53)),
        sa.CheckConstraint("state IN ('PREPARED','UNCERTAIN','VERIFIED')", name="publication_state_set"),
        sa.CheckConstraint("pr_number IS NULL OR pr_number > 0", name="publication_pr_positive"),
        sa.CheckConstraint(
            "(state='VERIFIED' AND pr_number IS NOT NULL AND pr_url IS NOT NULL "
            "AND verified_at IS NOT NULL) OR "
            "(state<>'VERIFIED' AND pr_number IS NULL AND pr_url IS NULL "
            "AND verified_at IS NULL)",
            name="publication_receipt_consistency",
        ),
    )


def downgrade() -> None:
    # Never destroy replay protection or forensic evidence with downgrade.
    raise RuntimeError("Destructive PostgreSQL baseline downgrade is forbidden; restore backup")
