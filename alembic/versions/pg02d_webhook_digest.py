"""Enforce one PostgreSQL inbox row per signed body."""

import sqlalchemy as sa

from alembic import op

revision = "pg02d_webhook_digest"
down_revision = "pg02c_agent_dispatch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep preflight and index replacement consistent with concurrent writers.
    op.execute("LOCK TABLE webhook_inbox IN ACCESS EXCLUSIVE MODE")
    duplicate = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM webhook_inbox GROUP BY body_digest HAVING count(*) > 1 LIMIT 1"
            )
        )
        .first()
    )
    if duplicate is not None:
        raise RuntimeError(
            "incompatible historical webhook inbox: duplicate body digests"
        )

    op.drop_index("webhook_inbox_body_digest", table_name="webhook_inbox")
    op.create_index(
        "webhook_inbox_body_digest", "webhook_inbox", ["body_digest"], unique=True
    )


def downgrade() -> None:
    raise RuntimeError("PostgreSQL webhook inbox uniqueness downgrade is forbidden")
