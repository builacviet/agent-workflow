"""Move Dev dispatch history into the role-neutral agent ledger."""

import sqlalchemy as sa

from alembic import op

revision = "pg02c_agent_dispatch"
down_revision = "pg01_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Renames preserve dispatch IDs and PostgreSQL foreign-key references,
    # including publication_intents and runtime evidence.
    op.rename_table("coder_dispatches", "agent_dispatches")
    op.rename_table("coder_runtime_evidence", "agent_execution_evidence")
    for name in ("role", "phase", "subject_type", "subject_id", "subject_version"):
        op.add_column("agent_dispatches", sa.Column(name, sa.Text(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE agent_dispatches SET role='dev', phase='code', "
            "subject_type='task', subject_id=task_id, "
            "subject_version=round_number::text || ':' || input_sha"
        )
    )
    for name in ("role", "phase", "subject_type", "subject_id", "subject_version"):
        op.alter_column("agent_dispatches", name, nullable=False)
    for name in ("task_id", "round_number", "input_sha"):
        op.alter_column("agent_dispatches", name, nullable=True)
    op.create_unique_constraint(
        "agent_dispatch_identity",
        "agent_dispatches",
        ["role", "phase", "subject_type", "subject_id", "subject_version"],
    )
    op.create_check_constraint(
        "agent_dev_identity",
        "agent_dispatches",
        "role <> 'dev' OR phase <> 'code' OR (task_id IS NOT NULL AND round_number IS NOT NULL AND input_sha IS NOT NULL AND subject_type = 'task' AND subject_id = task_id AND subject_version = round_number::text || ':' || input_sha)",
    )
    op.create_check_constraint(
        "agent_slot_state",
        "agent_dispatches",
        "(status IN ('ACTIVE','BLOCKED') AND slot IS NOT NULL AND slot = 1) OR (status IN ('DONE','ACKNOWLEDGED') AND slot IS NULL)",
    )
    op.add_column(
        "agent_execution_evidence", sa.Column("evidence_kind", sa.Text(), nullable=True)
    )
    op.add_column(
        "agent_execution_evidence",
        sa.Column("trusted_writer", sa.Text(), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE agent_execution_evidence SET evidence_kind='dev_runtime', trusted_writer='host'"
        )
    )
    op.alter_column("agent_execution_evidence", "evidence_kind", nullable=False)
    op.alter_column("agent_execution_evidence", "trusted_writer", nullable=False)
    for name in (
        "task_id",
        "input_sha",
        "runtime_name",
        "declared_runtime_version",
        "inference_telemetry",
        "tested_digest",
        "published_head_sha",
    ):
        op.alter_column("agent_execution_evidence", name, nullable=True)
    op.create_check_constraint(
        "agent_dev_evidence_fields",
        "agent_execution_evidence",
        "evidence_kind <> 'dev_runtime' OR (task_id IS NOT NULL AND input_sha IS NOT NULL AND runtime_name IS NOT NULL AND declared_runtime_version IS NOT NULL AND inference_telemetry IS NOT NULL AND tested_digest IS NOT NULL AND published_head_sha IS NOT NULL)",
    )
    # Reject inconsistent historical evidence instead of silently adopting it.
    mismatch = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM agent_execution_evidence e JOIN agent_dispatches d ON d.dispatch_id=e.dispatch_id WHERE e.task_id <> d.task_id OR e.input_sha <> d.input_sha OR (d.result_sha IS NOT NULL AND e.published_head_sha <> d.result_sha) LIMIT 1"
            )
        )
        .first()
    )
    if mismatch is not None:
        raise RuntimeError("incompatible historical dispatch evidence")


def downgrade() -> None:
    raise RuntimeError(
        "Destructive PostgreSQL dispatch downgrade is forbidden; restore backup"
    )
