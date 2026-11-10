"""Additive PostgreSQL foundation tests; no Docker, credentials or live DB."""
from pathlib import Path

import pytest

from agent_workflow.integrations.github import WorkflowError
from agent_workflow.storage.postgres_locks import controller_advisory_lock

ROOT = Path(__file__).resolve().parents[1]


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self.answer = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, query, parameters=()):
        if "pg_try_advisory_lock" in query:
            key = parameters
            self.answer = (key not in self.db.held,)
            if self.answer[0]:
                self.db.held.add(key)
        elif "pg_advisory_unlock" in query:
            self.db.held.discard(parameters)
            self.answer = (True,)
        else:
            self.answer = (1,) if self.db.available else None

    def fetchone(self):
        return self.answer


class FakeConnection:
    def __init__(self, db):
        self.db = db
        self.closed = False

    def cursor(self):
        if not self.db.available or self.closed:
            raise OSError("session unavailable")
        return FakeCursor(self.db)

    def close(self):
        self.closed = True


class FakeDB:
    def __init__(self):
        self.held = set()
        self.available = True

    def connect(self, dsn, autocommit=False):
        assert dsn == "postgresql://test" and autocommit is True
        return FakeConnection(self)


def test_independent_controller_role_locks_and_exclusivity():
    db = FakeDB()
    with controller_advisory_lock("postgresql://test", "dev_controller", connect=db.connect) as dev:
        dev.ensure_alive()
        with pytest.raises(WorkflowError, match="another trusted Controller"):
            with controller_advisory_lock("postgresql://test", "dev_controller", connect=db.connect):
                pass
        with controller_advisory_lock("postgresql://test", "ci_watcher", connect=db.connect) as ci:
            ci.ensure_alive()
            assert len(db.held) == 2
    assert not db.held


def test_failed_postgres_lock_session_blocks_further_actions():
    db = FakeDB()
    with controller_advisory_lock("postgresql://test", "dev_controller", connect=db.connect) as lease:
        db.available = False
        with pytest.raises(WorkflowError, match="session lost"):
            lease.ensure_alive()


def test_rejects_unknown_role_without_database_connection():
    db = FakeDB()
    with pytest.raises(ValueError, match="unsupported"):
        with controller_advisory_lock("postgresql://test", "webhook", connect=db.connect):
            pass


def test_postgres_sql_contract_roles_and_separate_schema():
    base = ROOT / "db/postgres"
    roles = (base / "000_roles.sql").read_text()
    schema = (base / "001_schema.sql").read_text()
    grants = (base / "002_grants.sql").read_text()
    assert "NOLOGIN" in roles
    assert "REVOKE ALL ON DATABASE agent_workflow FROM PUBLIC" in roles
    for role in ("mcp_user", "ci_user", "webhook_user", "controller_user"):
        assert role in grants
    for table in (
        "tasks", "review_attempts", "reviews", "review_findings",
        "task_events", "coder_dispatches", "coder_runtime_evidence",
        "webhook_inbox", "controller_ci_tasks", "controller_ci_observations",
        "controller_ci_processed",
    ):
        assert ("CREATE TABLE IF NOT EXISTS " + table) in schema
    assert "UNIQUE INDEX IF NOT EXISTS webhook_inbox_body_digest" in schema
    assert "db_migrator" in roles
    assert "GRANT ALL" not in grants
    assert "GRANT SELECT, INSERT" in grants
    assert "TO coder_worker" not in grants


def test_postgres_compose_keeps_sqlite_stack_unmodified():
    compose = (ROOT / "compose.postgres.yaml").read_text()
    assert "profiles: [database]" in compose
    assert "127.0.0.1:15432:5432" in compose
    assert "POSTGRES_PASSWORD_FILE" in compose
    assert "postgres_admin_password" in compose
