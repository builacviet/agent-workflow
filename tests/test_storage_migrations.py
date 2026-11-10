"""Migration runner regression, across all workflow and webhook SQLite tables."""
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

import pytest

from agent_workflow.storage.repository import SQLiteWorkflowRepository
from agent_workflow.storage.webhook_inbox import WebhookInbox


def _rows(path: Path):
    with closing(sqlite3.connect(path)) as db:
        return db.execute(
            "SELECT component, version, name FROM schema_migrations "
            "ORDER BY component, version"
        ).fetchall()


def test_workflow_dispatch_and_webhook_share_migration_registry():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "shared.sqlite"
        SQLiteWorkflowRepository(path)
        WebhookInbox(path)
        SQLiteWorkflowRepository(path)
        WebhookInbox(path)
        assert _rows(path) == [
            ("webhook", 1, "webhook_inbox"),
            ("workflow", 1, "workflow_baseline"),
            ("workflow", 2, "coder_dispatches"),
        ]
        with closing(sqlite3.connect(path)) as db:
            names = {
                name for (name,) in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert {
                "schema_migrations", "tasks", "review_attempts", "reviews",
                "review_findings", "task_events", "coder_dispatches",
                "webhook_inbox",
            } <= names


def test_workflow_legacy_tables_are_adopted_without_losing_data():
    from agent_workflow.storage.workflow_migrations import (
        _CORE_SQL, _DISPATCH_SQL,
    )
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "legacy.sqlite"
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript(_CORE_SQL)
            db.execute(_DISPATCH_SQL)
            db.execute(
                "INSERT INTO tasks(task_id,metadata,repository,branch,"
                "base_sha,requested_effort,require_verified_effort,status,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("T-1", "{}", "o/r", "b", "a"*40,
                 "medium", 0, "AWAITING_HANDOFF", 1.0, 1.0),
            )
        SQLiteWorkflowRepository(path)
        with closing(sqlite3.connect(path)) as db:
            assert db.execute(
                "SELECT task_id FROM tasks"
            ).fetchall() == [("T-1",)]
        assert _rows(path) == [
            ("workflow", 1, "workflow_baseline"),
            ("workflow", 2, "coder_dispatches"),
        ]


def test_workflow_legacy_core_without_dispatch_gets_second_migration():
    from agent_workflow.storage.workflow_migrations import _CORE_SQL
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "legacy.sqlite"
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript(_CORE_SQL)
        SQLiteWorkflowRepository(path)
        assert [r[1] for r in _rows(path)] == [1, 2]


def test_unknown_workflow_schema_fails_closed_without_data_loss():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "unexpected.sqlite"
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE tasks (task_id TEXT PRIMARY KEY)")
            db.execute("INSERT INTO tasks VALUES ('do-not-delete')")
        with pytest.raises(sqlite3.DatabaseError):
            SQLiteWorkflowRepository(path)
        with closing(sqlite3.connect(path)) as db:
            assert db.execute(
                "SELECT task_id FROM tasks"
            ).fetchall() == [("do-not-delete",)]
            assert db.execute(
                "SELECT name FROM sqlite_master WHERE name='schema_migrations'"
            ).fetchone() is None


def test_incomplete_or_future_migration_history_fails_closed():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "future.sqlite"
        SQLiteWorkflowRepository(path)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute(
                "UPDATE schema_migrations SET version=99 "
                "WHERE component='workflow' AND version=2"
            )
        with pytest.raises(sqlite3.DatabaseError):
            SQLiteWorkflowRepository(path)
