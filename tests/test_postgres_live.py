"""Real PostgreSQL bootstrap, permission-denial and advisory-lock tests.

Only runs against an isolated explicit test database. Never connects to
operator/production database. GitHub Actions provisions an ephemeral PG service.
"""
import os
from pathlib import Path

import pytest

from agent_workflow.integrations.github import WorkflowError
from agent_workflow.storage.postgres_locks import controller_advisory_lock

ROOT = Path(__file__).resolve().parents[1]
DSN = os.getenv("AGENT_WORKFLOW_TEST_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="isolated PostgreSQL fixture unavailable")


@pytest.fixture(scope="module")
def pg():
    import psycopg

    conn = psycopg.connect(DSN, autocommit=True)
    # Absolute safety check: never bootstrap a local operator's live DB.
    with conn.cursor() as cur:
        cur.execute("SELECT current_database(), current_user")
        database, login = cur.fetchone()
        if database != "agent_workflow" or login != "postgres":
            conn.close()
            pytest.fail("unexpected PostgreSQL target; refusing schema bootstrap")
        cur.execute("SELECT count(*) FROM pg_namespace WHERE nspname='workflow'")
        if cur.fetchone()[0]:
            conn.close()
            pytest.fail("non-empty PostgreSQL target; refusing schema bootstrap")
    for name in ("000_roles.sql", "001_schema.sql", "002_grants.sql"):
        script = (ROOT / "db/postgres" / name).read_text(encoding="utf-8")
        with conn.cursor() as cur:
            cur.execute(script, prepare=False)
    try:
        yield conn
    finally:
        conn.close()


def test_postgres_schema_bootstrap_and_roles(pg):
    with pg.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='workflow'")
        tables = {row[0] for row in cur.fetchall()}
        assert {
            "tasks", "review_attempts", "reviews", "review_findings",
            "task_events", "coder_dispatches", "coder_runtime_evidence",
            "webhook_inbox", "controller_ci_tasks", "controller_ci_observations",
            "controller_ci_processed", "schema_migrations",
        } <= tables
        cur.execute(
            "SELECT rolname, rolcanlogin, rolsuper, rolcreatedb "
            "FROM pg_roles WHERE rolname = ANY(%s)",
            (["mcp_user", "webhook_user", "ci_user",
              "controller_user", "db_migrator"],),
        )
        roles = cur.fetchall()
        assert len(roles) == 5
        assert all(not canlogin and not superuser and not createdb
                   for _, canlogin, superuser, createdb in roles)


@pytest.mark.parametrize("role,table,privilege,allowed", [
    ("webhook_user", "workflow.webhook_inbox", "INSERT", True),
    ("webhook_user", "workflow.tasks", "SELECT", False),
    ("ci_user", "workflow.tasks", "SELECT", True),
    ("ci_user", "workflow.tasks", "UPDATE", False),
    ("ci_user", "workflow.controller_ci_tasks", "UPDATE", True),
    ("ci_user", "workflow.webhook_inbox", "INSERT", False),
    ("mcp_user", "workflow.controller_ci_tasks", "SELECT", True),
    ("mcp_user", "workflow.controller_ci_tasks", "UPDATE", False),
    ("controller_user", "workflow.coder_dispatches", "UPDATE", True),
    ("controller_user", "workflow.webhook_inbox", "INSERT", False),
])
def test_postgres_least_privilege_grants(pg, role, table, privilege, allowed):
    with pg.cursor() as cur:
        cur.execute("SELECT has_table_privilege(%s, %s, %s)",
                    (role, table, privilege))
        assert cur.fetchone()[0] is allowed


def test_postgres_runtime_role_cannot_execute_ddl(pg):
    import psycopg

    with pg.cursor() as cur:
        cur.execute("SET ROLE webhook_user")
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute("CREATE TABLE workflow.not_allowed (id integer)")
        finally:
            cur.execute("RESET ROLE")


def test_postgres_inbox_digest_unique_under_racing_delivery_ids(pg):
    import psycopg

    with pg.cursor() as cur:
        cur.execute(
            "INSERT INTO workflow.webhook_inbox "
            "(delivery_id,event,action,repository,installation_id,subject_id,head_sha,body_digest) "
            "VALUES('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa','pull_request','synchronize',"
            "'builacviet/agent-workflow',17,21,repeat('a',40),repeat('b',64))"
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            cur.execute(
                "INSERT INTO workflow.webhook_inbox "
                "(delivery_id,event,action,repository,installation_id,subject_id,head_sha,body_digest) "
                "VALUES('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb','pull_request','synchronize',"
                "'builacviet/agent-workflow',17,22,repeat('a',40),repeat('b',64))"
            )


def test_postgres_concurrent_controller_advisory_locks(pg):
    with controller_advisory_lock(DSN, "dev_controller") as dev:
        dev.ensure_alive()
        with pytest.raises(WorkflowError, match="another trusted Controller"):
            with controller_advisory_lock(DSN, "dev_controller"):
                pass
        with controller_advisory_lock(DSN, "ci_watcher") as ci:
            ci.ensure_alive()

def test_postgres_workflow_service_adapter_provisions_and_handoffs(pg):
    from agent_workflow.domain.service import WorkflowService
    from agent_workflow.storage.postgres_workflow import PostgresWorkflowRepository

    repo = PostgresWorkflowRepository(DSN)
    service = WorkflowService(repository=repo, db_path=DSN)
    spec = {
        "task_id": "PG-TEST-001",
        "title": "PostgreSQL workflow",
        "description": "Isolated CI proof",
        "acceptance_criteria": ["AC"],
        "definition_of_done": "verified",
        "repository": "builacviet/agent-workflow",
        "branch": "codex/pg-test",
        "base_sha": "a" * 40,
        "requested_effort": "high",
        "risk_level": "high",
        "require_verified_effort": True,
        "context_paths": ["docs/handoff.md"],
        "synthetic": True,
    }
    service.provision_task(spec)
    # Idempotent authorization and consistent status in independent connections.
    WorkflowService(repository=PostgresWorkflowRepository(DSN),
                    db_path=DSN).provision_task(spec)
    service.register_task_handoff(
        "PG-TEST-001", "builacviet/agent-workflow", "codex/pg-test",
        "a" * 40, "b" * 40, "docs/handoff.md", "CI verified",
    )
    with repo.read() as db:
        task = db.find_task(("PG-TEST-001",))
        assert task["status"] == "READY_IN_REVIEW"
        assert task["current_head_sha"] == "b" * 40
        assert len(db.list_events(("PG-TEST-001",))) == 2


def test_postgres_workflow_adapter_rolls_back_failed_transaction(pg):
    from agent_workflow.storage.postgres_workflow import PostgresWorkflowRepository

    repo = PostgresWorkflowRepository(DSN)
    with pytest.raises(RuntimeError, match="rollback"):
        with repo.raw_transaction() as db:
            db.execute(
                "INSERT INTO tasks(task_id,metadata,repository,branch,base_sha,"
                "requested_effort,require_verified_effort,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("PG-ROLLBACK", "{}", "builacviet/agent-workflow", "codex/pg",
                 "a" * 40, "high", 1, "AWAITING_HANDOFF", 0.0, 0.0),
            )
            raise RuntimeError("rollback")
    with repo.read() as db:
        assert db.execute("SELECT 1 FROM tasks WHERE task_id=?", ("PG-ROLLBACK",)).fetchone() is None
