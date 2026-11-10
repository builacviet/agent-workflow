"""Schema migrations for workflow/review and dispatch persistence."""
from contextlib import closing
from pathlib import Path
import sqlite3

from agent_workflow.integrations.github import WorkflowError
from agent_workflow.storage.migrations import (
    Migration, execute_sql_script, migrate, table_names, validate_schema,
)

LEGACY_WORKFLOW_TABLES = frozenset({
    "tasks", "review_attempts", "reviews", "review_findings",
    "task_events", "coder_dispatches", "coder_runtime_evidence",
})
# schema.sql is the frozen v1 baseline. Later versions belong in new migrations.
_CORE_SQL = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
_DISPATCH_SQL = "CREATE TABLE IF NOT EXISTS coder_dispatches (\n    dispatch_id TEXT PRIMARY KEY, task_id TEXT NOT NULL,\n    round_number INTEGER NOT NULL, input_sha TEXT NOT NULL,\n    status TEXT NOT NULL CHECK(status IN ('ACTIVE','BLOCKED','DONE','ACKNOWLEDGED')),\n    slot INTEGER UNIQUE CHECK(slot=1), deadline REAL NOT NULL,\n    result_sha TEXT, detail TEXT NOT NULL DEFAULT '',\n    UNIQUE(task_id,round_number,input_sha)\n)"

_RUNTIME_EVIDENCE_SQL = """
    CREATE TABLE IF NOT EXISTS coder_runtime_evidence (
        dispatch_id TEXT PRIMARY KEY REFERENCES coder_dispatches(dispatch_id),
        task_id TEXT NOT NULL, input_sha TEXT NOT NULL,
        runtime_name TEXT NOT NULL, declared_runtime_version TEXT NOT NULL,
        observed_image_id TEXT, inference_telemetry TEXT NOT NULL,
        tested_digest TEXT NOT NULL, published_head_sha TEXT NOT NULL,
        recorded_at REAL NOT NULL
    );
"""



def _validate_workflow(db: sqlite3.Connection, version: int) -> None:
    with closing(sqlite3.connect(":memory:")) as expected:
        for step in WORKFLOW_MIGRATIONS[:version]:
            step.upgrade(expected)
        validate_schema(db, expected, "workflow")


def _legacy_workflow(db: sqlite3.Connection) -> int:
    if db.execute("PRAGMA user_version").fetchone()[0] not in (0, 1):
        raise WorkflowError("unsupported database schema version")
    names = table_names(db)
    if "coder_runtime_evidence" in names and "coder_dispatches" not in names:
        raise sqlite3.DatabaseError("orphaned runtime evidence table")
    core_tables = LEGACY_WORKFLOW_TABLES - {"coder_dispatches", "coder_runtime_evidence"}
    if names & core_tables:
        version = 2 if "coder_dispatches" in names else 1
        _validate_workflow(db, version)
        return version
    if "coder_dispatches" in names:
        raise sqlite3.DatabaseError("orphaned dispatch table")
    # Webhook owns validation and retirement of its legacy aliases.
    if names - {"schema_migrations", "webhook_inbox", "webhook_delivery_aliases"}:
        raise sqlite3.DatabaseError("unexpected legacy workflow tables")
    if "webhook_delivery_aliases" in names and "webhook_inbox" not in names:
        raise sqlite3.DatabaseError("orphaned legacy webhook aliases")
    return 0


WORKFLOW_MIGRATIONS = (
    Migration(1, "workflow_baseline", lambda db: execute_sql_script(db, _CORE_SQL)),
    Migration(2, "coder_dispatches", lambda db: db.execute(_DISPATCH_SQL)),
    # Adopt valid pre-registry evidence in place. Final schema validation in
    # the migration transaction rejects mismatched DDL and attached triggers.
    Migration(3, "coder_runtime_evidence", lambda db: db.execute(_RUNTIME_EVIDENCE_SQL)),
)


def migrate_workflow(db: sqlite3.Connection) -> None:
    migrate(
        db, "workflow", WORKFLOW_MIGRATIONS,
        adopt_legacy=_legacy_workflow,
        validate=_validate_workflow,
    )
