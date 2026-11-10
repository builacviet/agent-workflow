"""Migration runner regression, across all workflow and webhook SQLite tables."""
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

import pytest

from agent_workflow.domain.service import WorkflowService
from agent_workflow.integrations.github import WorkflowError
from agent_workflow.storage import workflow_migrations
from agent_workflow.storage.dispatch_repository import DispatchLedger
from agent_workflow.storage.migrations import Migration
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
            ("workflow", 3, "coder_runtime_evidence"),
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
                "webhook_inbox", "coder_runtime_evidence",
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
            ("workflow", 3, "coder_runtime_evidence"),
        ]


def test_workflow_legacy_core_without_dispatch_gets_second_migration():
    from agent_workflow.storage.workflow_migrations import _CORE_SQL
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "legacy.sqlite"
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript(_CORE_SQL)
        SQLiteWorkflowRepository(path)
        assert [r[1] for r in _rows(path)] == [1, 2, 3]


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


@pytest.mark.parametrize("version", [0, 1, 2, 99])
def test_legacy_workflow_version_gate_preserves_marker_and_schema(tmp_path, version):
    path = tmp_path / "legacy.sqlite"
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(workflow_migrations._CORE_SQL)
        db.execute(f"PRAGMA user_version = {version}")
    if version in (0, 1):
        SQLiteWorkflowRepository(path)
        assert [row[1] for row in _rows(path)] == [1, 2, 3]
    else:
        with pytest.raises(WorkflowError, match="unsupported database schema version"):
            SQLiteWorkflowRepository(path)
        with closing(sqlite3.connect(path)) as db:
            assert db.execute(
                "SELECT name FROM sqlite_master WHERE name IN "
                "('schema_migrations', 'coder_dispatches')"
            ).fetchall() == []
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (version,)


def test_legacy_workflow_accepts_sql_formatting_and_identifier_case(tmp_path):
    path = tmp_path / "formatted.sqlite"
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(workflow_migrations._CORE_SQL.replace(
            "CREATE TABLE IF NOT EXISTS tasks", "create table if not exists TASKS"
        ).replace("status TEXT", "STATUS    text"))
    SQLiteWorkflowRepository(path)
    assert [row[1] for row in _rows(path)] == [1, 2, 3]


def test_workflow_schema_rejects_unicode_whitespace_inside_identifier(tmp_path):
    path = tmp_path / "changed-column.sqlite"
    with closing(sqlite3.connect(path)) as db, db:
        # SQLite treats the non-breaking space as part of the identifier.
        db.executescript(workflow_migrations._CORE_SQL.replace(
            "metadata TEXT", "metadata\u00a0TEXT"
        ))
    with pytest.raises(sqlite3.DatabaseError, match="incompatible workflow schema"):
        SQLiteWorkflowRepository(path)


def test_workflow_schema_rejects_case_changed_check_literal(tmp_path):
    path = tmp_path / "changed-check.sqlite"
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(workflow_migrations._CORE_SQL.replace(
            "'AWAITING_HANDOFF'", "'awaiting_handoff'"
        ))
    with pytest.raises(sqlite3.DatabaseError, match="incompatible workflow schema"):
        SQLiteWorkflowRepository(path)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='schema_migrations'"
        ).fetchone() is None



def test_dispatch_cr_comment_does_not_hide_slot_check(tmp_path):
    """SQLite -- comments end at LF, not CR; reject a dropped CHECK constraint."""
    path = tmp_path / "bad-legacy-dispatch.sqlite"
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(workflow_migrations._CORE_SQL)
        altered = workflow_migrations._DISPATCH_SQL.replace(
            "slot INTEGER UNIQUE CHECK(slot=1),",
            "slot INTEGER UNIQUE --\rCHECK(slot=1)\n,",
        )
        assert altered != workflow_migrations._DISPATCH_SQL
        db.execute(altered)
        # Prove SQLite no longer enforces CHECK(slot=1) in the tampered DDL.
        db.execute(
            "INSERT INTO coder_dispatches("
            "dispatch_id,task_id,round_number,input_sha,status,slot,deadline"
            ") VALUES ('D-2','T-2',1,'sha','ACTIVE',2,300)"
        )

    with pytest.raises(sqlite3.DatabaseError, match="incompatible workflow schema"):
        SQLiteWorkflowRepository(path)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT slot FROM coder_dispatches").fetchall() == [(2,)]
        assert db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='schema_migrations'"
        ).fetchone() is None


@pytest.fixture
def workflow_v5(monkeypatch):
    monkeypatch.setattr(
        workflow_migrations,
        "WORKFLOW_MIGRATIONS",
        workflow_migrations.WORKFLOW_MIGRATIONS + (
            Migration(4, "workflow_fixture_table", lambda db: db.execute(
                "CREATE TABLE workflow_fixture (value TEXT NOT NULL)"
            )),
            Migration(5, "workflow_fixture_column", lambda db: db.execute(
                "ALTER TABLE tasks ADD COLUMN fixture_label TEXT NOT NULL DEFAULT 'New Value'"
            )),
        ),
    )


@pytest.mark.parametrize("start", ["fresh", "legacy", "registered"])
def test_workflow_v4_v5_evolution_reopens_both_adapters(tmp_path, start, request):
    path = tmp_path / "evolution.sqlite"
    if start == "registered":
        SQLiteWorkflowRepository(path)
    elif start == "legacy":
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript(workflow_migrations._CORE_SQL)
    if start != "fresh":
        with closing(sqlite3.connect(path)) as db, db:
            db.execute(
                "INSERT INTO tasks(task_id,metadata,repository,branch,base_sha,"
                "requested_effort,require_verified_effort,status,created_at,updated_at) "
                "VALUES('T-1','{}','o/r','b','sha','medium',0,'AWAITING_HANDOFF',1,1)"
            )
    request.getfixturevalue("workflow_v5")

    SQLiteWorkflowRepository(path)
    # First-time webhook adoption and reopening must both allow v4 objects.
    WebhookInbox(path)
    rows = _rows(path)
    assert [version for component, version, _ in rows if component == "workflow"] == [
        1, 2, 3, 4, 5
    ]
    with closing(sqlite3.connect(path)) as db, db:
        assert db.execute("SELECT * FROM workflow_fixture").fetchall() == []
        assert db.execute("SELECT fixture_label FROM tasks").fetchall() == (
            [] if start == "fresh" else [("New Value",)]
        )
        db.execute("INSERT INTO workflow_fixture VALUES ('preserved')")
    SQLiteWorkflowRepository(path)
    WebhookInbox(path)
    assert _rows(path) == rows
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT * FROM workflow_fixture").fetchall() == [
            ("preserved",)
        ]


def test_failed_workflow_upgrade_rolls_back_ddl_and_migration_history(tmp_path, monkeypatch):
    path = tmp_path / "rollback.sqlite"
    SQLiteWorkflowRepository(path)
    before = _rows(path)

    def fail_upgrade(db):
        db.execute("ALTER TABLE tasks ADD COLUMN aborted TEXT")
        raise sqlite3.DatabaseError("injected migration failure")

    monkeypatch.setattr(
        workflow_migrations,
        "WORKFLOW_MIGRATIONS",
        workflow_migrations.WORKFLOW_MIGRATIONS + (
            Migration(4, "rollback_fixture", lambda db: db.execute(
                "CREATE TABLE rollback_fixture (value TEXT)"
            )),
            Migration(5, "aborted_upgrade", fail_upgrade),
        ),
    )
    with pytest.raises(sqlite3.DatabaseError, match="injected migration failure"):
        SQLiteWorkflowRepository(path)
    assert _rows(path) == before
    with closing(sqlite3.connect(path)) as db:
        assert db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='rollback_fixture'"
        ).fetchone() is None
        assert "aborted" not in {row[1] for row in db.execute("PRAGMA table_info(tasks)")}


_LEGACY_EVIDENCE_SQL = """
    CREATE TABLE coder_runtime_evidence (
        dispatch_id TEXT PRIMARY KEY REFERENCES coder_dispatches(dispatch_id),
        task_id TEXT NOT NULL, input_sha TEXT NOT NULL,
        runtime_name TEXT NOT NULL, declared_runtime_version TEXT NOT NULL,
        observed_image_id TEXT, inference_telemetry TEXT NOT NULL,
        tested_digest TEXT NOT NULL, published_head_sha TEXT NOT NULL,
        recorded_at REAL NOT NULL
    )
"""
_DISPATCH_ID = "a" * 32
_EVIDENCE = (_DISPATCH_ID, "T-1", "b" * 40, "codex", "1.0",
             None, "UNVERIFIED", "c" * 64, "d" * 40)


def _open_v2(path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(workflow_migrations, "WORKFLOW_MIGRATIONS",
                      workflow_migrations.WORKFLOW_MIGRATIONS[:2])
        SQLiteWorkflowRepository(path)


def _insert_active_dispatch(db):
    db.execute(
        "INSERT INTO coder_dispatches"
        "(dispatch_id,task_id,round_number,input_sha,status,slot,deadline) "
        "VALUES(?,?,0,?,'ACTIVE',1,300)",
        _EVIDENCE[:3],
    )


@pytest.mark.parametrize("has_legacy_evidence", [False, True])
def test_v2_upgrade_creates_or_preserves_runtime_evidence(tmp_path, monkeypatch, has_legacy_evidence):
    path = tmp_path / "shared-v2.sqlite"
    _open_v2(path, monkeypatch)
    WebhookInbox(path)
    with closing(sqlite3.connect(path)) as db, db:
        _insert_active_dispatch(db)
        if has_legacy_evidence:
            db.execute(_LEGACY_EVIDENCE_SQL)
            db.execute(
                "INSERT INTO coder_runtime_evidence VALUES(?,?,?,?,?,?,?,?,?,?)",
                (*_EVIDENCE, 123.0),
            )
    repo = SQLiteWorkflowRepository(path)
    WebhookInbox(path)
    with repo.raw_connection() as db:
        rows = [tuple(row) for row in db.execute("SELECT * FROM coder_runtime_evidence")]
    assert rows == ([(*_EVIDENCE, 123.0)] if has_legacy_evidence else [])
    assert _rows(path)[-1] == ("workflow", 3, "coder_runtime_evidence")


@pytest.mark.parametrize("corruption", ["table", "trigger"])
def test_evidence_migration_rejects_incompatible_schema(tmp_path, monkeypatch, corruption):
    path = tmp_path / "bad-evidence.sqlite"
    _open_v2(path, monkeypatch)
    before = _rows(path)
    with closing(sqlite3.connect(path)) as db, db:
        if corruption == "table":
            db.execute("CREATE TABLE coder_runtime_evidence (dispatch_id TEXT PRIMARY KEY)")
        else:
            db.execute(_LEGACY_EVIDENCE_SQL)
            db.execute("""
                CREATE TRIGGER ignore_evidence BEFORE INSERT ON coder_runtime_evidence
                BEGIN SELECT RAISE(IGNORE); END
            """)
    with pytest.raises(sqlite3.DatabaseError, match="incompatible workflow schema"):
        SQLiteWorkflowRepository(path)
    assert _rows(path) == before


def test_ignored_evidence_insert_raises_and_rolls_back(tmp_path):
    service = WorkflowService(tmp_path / "evidence.sqlite")
    ledger = DispatchLedger(service)
    with service.repository.raw_transaction() as db:
        _insert_active_dispatch(db)
        db.execute("""
            CREATE TRIGGER ignore_evidence BEFORE INSERT ON coder_runtime_evidence
            BEGIN
                UPDATE coder_dispatches SET detail='rollback' WHERE dispatch_id=NEW.dispatch_id;
                SELECT RAISE(IGNORE);
            END
        """)
    with pytest.raises(WorkflowError, match="runtime evidence was not inserted"):
        ledger.record_runtime_evidence(*_EVIDENCE)
    with service.repository.raw_connection() as db:
        assert db.execute("SELECT count(*) FROM coder_runtime_evidence").fetchone()[0] == 0
        assert db.execute("SELECT detail FROM coder_dispatches").fetchone()[0] == ""
