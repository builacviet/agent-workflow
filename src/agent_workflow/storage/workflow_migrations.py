"""Schema migrations for workflow/review and dispatch persistence."""
from pathlib import Path
import sqlite3

from agent_workflow.storage.migrations import (
    Migration, execute_sql_script, migrate, table_names,
)

_CORE_OBJECTS = frozenset({
    "tasks", "review_attempts", "reviews", "review_findings",
    "task_events", "one_active_claim",
})
_CORE_SQL = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
_DISPATCH_SQL = "CREATE TABLE IF NOT EXISTS coder_dispatches (\n    dispatch_id TEXT PRIMARY KEY, task_id TEXT NOT NULL,\n    round_number INTEGER NOT NULL, input_sha TEXT NOT NULL,\n    status TEXT NOT NULL CHECK(status IN ('ACTIVE','BLOCKED','DONE','ACKNOWLEDGED')),\n    slot INTEGER UNIQUE CHECK(slot=1), deadline REAL NOT NULL,\n    result_sha TEXT, detail TEXT NOT NULL DEFAULT '',\n    UNIQUE(task_id,round_number,input_sha)\n)"
_DISPATCH_COLUMNS = (
    ("dispatch_id", "TEXT", 0, 1),
    ("task_id", "TEXT", 1, 0),
    ("round_number", "INTEGER", 1, 0),
    ("input_sha", "TEXT", 1, 0),
    ("status", "TEXT", 1, 0),
    ("slot", "INTEGER", 0, 0),
    ("deadline", "REAL", 1, 0),
    ("result_sha", "TEXT", 0, 0),
    ("detail", "TEXT", 1, 0),
)


def _core_objects(db: sqlite3.Connection) -> dict[str, str]:
    return {
        name: "".join(sql.lower().split())
        for name, sql in db.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE name IN (" + ",".join("?" for _ in _CORE_OBJECTS) + ")",
            tuple(sorted(_CORE_OBJECTS)),
        )
        if sql is not None
    }


def _validate_core(db: sqlite3.Connection) -> None:
    with sqlite3.connect(":memory:") as expected_db:
        execute_sql_script(expected_db, _CORE_SQL)
        expected = _core_objects(expected_db)
    if _core_objects(db) != expected or set(expected) != _CORE_OBJECTS:
        raise sqlite3.DatabaseError("incompatible workflow schema")


def _validate_dispatch(db: sqlite3.Connection) -> None:
    from agent_workflow.storage.migrations import table_columns
    if table_columns(db, "coder_dispatches") != _DISPATCH_COLUMNS:
        raise sqlite3.DatabaseError("incompatible dispatch schema")
    statement = db.execute(
        "SELECT sql FROM sqlite_master WHERE name='coder_dispatches'"
    ).fetchone()
    if statement is None or statement[0] is None:
        raise sqlite3.DatabaseError("missing dispatch table")
    # SQLite removes IF NOT EXISTS from sqlite_master.sql. Compare the
    # complete DDL after normalizing only that prefix and whitespace.
    def canonical(sql: str) -> str:
        compact = "".join(sql.lower().split())
        return compact.replace("createtableifnotexists", "createtable", 1)

    if canonical(statement[0]) != canonical(_DISPATCH_SQL):
        raise sqlite3.DatabaseError("incompatible dispatch constraints")


def _legacy_workflow(db: sqlite3.Connection) -> int:
    names = table_names(db)
    present = names & _CORE_OBJECTS
    if present:
        _validate_core(db)
        if "coder_dispatches" in names:
            _validate_dispatch(db)
            return 2
        return 1
    if "coder_dispatches" in names:
        raise sqlite3.DatabaseError("orphaned dispatch table")
    # An empty database or another migrated component is allowed.
    if names - {"schema_migrations", "webhook_inbox"}:
        raise sqlite3.DatabaseError("unexpected legacy workflow tables")
    return 0


def _validate_workflow(db: sqlite3.Connection) -> None:
    _validate_core(db)
    _validate_dispatch(db)


WORKFLOW_MIGRATIONS = (
    Migration(1, "workflow_baseline", lambda db: execute_sql_script(db, _CORE_SQL)),
    Migration(2, "coder_dispatches", lambda db: db.execute(_DISPATCH_SQL)),
)


def migrate_workflow(db: sqlite3.Connection) -> None:
    migrate(
        db, "workflow", WORKFLOW_MIGRATIONS,
        adopt_legacy=_legacy_workflow,
        validate=_validate_workflow,
    )
