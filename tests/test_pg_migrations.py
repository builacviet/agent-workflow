"""Independent PostgreSQL/Alembic foundation tests.

The live tests run only against an explicitly provided, disposable PostgreSQL
instance. They never connect to any operator's production deployment.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

sa = pytest.importorskip("sqlalchemy")


def _test_engine():
    dsn = os.environ.get("WFC_PG_TEST_DSN")
    if not dsn:
        pytest.skip("disposable WFC_PG_TEST_DSN not supplied")
    url = sa.engine.make_url(dsn)
    if url.drivername != "postgresql+psycopg" or not url.database:
        pytest.fail("WFC_PG_TEST_DSN must be a PostgreSQL+psycopg test DSN")
    return sa.create_engine(dsn, pool_pre_ping=True)


def test_alembic_has_no_checked_in_database_credentials():
    root = Path(__file__).resolve().parents[1]
    ini = (root / "alembic.ini").read_text(encoding="utf-8")
    env = (root / "alembic" / "env.py").read_text(encoding="utf-8")
    migration = (root / "alembic" / "versions" / "pg01_baseline.py").read_text(
        encoding="utf-8"
    )
    assert "sqlalchemy.url =" not in ini
    assert "WFC_PG_MIGRATOR_DSN" in env
    assert "CREATE SCHEMA wfc" in migration
    assert "Destructive PostgreSQL baseline downgrade is forbidden" in migration
    assert "from flask " not in env.lower()
    assert "import flask" not in env.lower()


def test_postgres_baseline_tables_and_version():
    engine = _test_engine()
    try:
        inspector = sa.inspect(engine)
        expected = {
            "tasks", "review_attempts", "reviews", "review_findings",
            "task_events", "coder_dispatches", "coder_runtime_evidence",
            "webhook_inbox", "controller_ci_tasks", "controller_ci_observations",
            "controller_ci_processed", "publication_intents",
        }
        assert set(inspector.get_table_names(schema="wfc")) == expected
        assert "tasks" not in inspector.get_table_names(schema="public")
        assert "schema_migrations" not in inspector.get_table_names(schema="wfc")
        assert any(
            idx["name"] == "one_active_claim" and idx["unique"]
            for idx in inspector.get_indexes("review_attempts", schema="wfc")
        )
        with engine.connect() as conn:
            assert conn.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one() == "pg01_baseline"
    finally:
        engine.dispose()


def test_postgres_dispatch_global_slot_unique_across_controllers():
    """A BLOCKED or ACTIVE dispatch reserves the slot despite another claimant."""
    from sqlalchemy.exc import IntegrityError

    engine = _test_engine()
    try:
        with engine.connect() as conn:
            transaction = conn.begin()
            try:
                conn.execute(sa.text(
                    "INSERT INTO wfc.tasks(task_id,metadata,repository,branch,base_sha,"
                    "requested_effort,require_verified_effort,status,created_at,updated_at) "
                    "VALUES ('PG01_SLOT_TEST','{}','owner/repo','codex/example',:sha,"
                    "'high',1,'AWAITING_HANDOFF',0.0,0.0)"
                ), {"sha": "a" * 40})
                conn.execute(sa.text(
                    "INSERT INTO wfc.coder_dispatches "
                    "(dispatch_id,task_id,round_number,input_sha,status,slot,deadline) "
                    "VALUES ('PG01_DISPATCH_A','PG01_SLOT_TEST',1,:sha,'ACTIVE',1,100.0)"
                ), {"sha": "b" * 40})
                with pytest.raises(IntegrityError):
                    with conn.begin_nested():
                        conn.execute(sa.text(
                            "INSERT INTO wfc.coder_dispatches "
                            "(dispatch_id,task_id,round_number,input_sha,status,slot,deadline) "
                            "VALUES ('PG01_DISPATCH_B','PG01_SLOT_TEST',2,:sha,'BLOCKED',1,100.0)"
                        ), {"sha": "c" * 40})
                assert conn.execute(sa.text(
                    "SELECT COUNT(*) FROM wfc.coder_dispatches "
                    "WHERE dispatch_id = 'PG01_DISPATCH_A'"
                )).scalar_one() == 1
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


def test_postgres_rejects_publication_receipt_without_verified_state():
    from sqlalchemy.exc import IntegrityError

    engine = _test_engine()
    try:
        with engine.connect() as conn:
            tx = conn.begin()
            try:
                conn.execute(sa.text(
                    "INSERT INTO wfc.tasks(task_id,metadata,repository,branch,base_sha,"
                    "requested_effort,require_verified_effort,status,created_at,updated_at) "
                    "VALUES ('PG01_PUBLISH_TEST','{}','owner/repo','codex/example',:sha,"
                    "'high',1,'AWAITING_HANDOFF',0.0,0.0)"
                ), {"sha": "a" * 40})
                conn.execute(sa.text(
                    "INSERT INTO wfc.coder_dispatches "
                    "(dispatch_id,task_id,round_number,input_sha,status,slot,deadline) "
                    "VALUES ('PG01_PUBLISH_DISPATCH','PG01_PUBLISH_TEST',1,:sha,"
                    "'BLOCKED',NULL,100.0)"
                ), {"sha": "b" * 40})
                with pytest.raises(IntegrityError):
                    with conn.begin_nested():
                        conn.execute(sa.text(
                            "INSERT INTO wfc.publication_intents "
                            "(dispatch_id,task_id,repository,branch,base_sha,input_sha,"
                            "expected_head_sha,tested_digest,state,pr_number,pr_url,"
                            "created_at,verified_at) VALUES "
                            "('PG01_PUBLISH_DISPATCH','PG01_PUBLISH_TEST','owner/repo',"
                            "'codex/example',:base,:input,:head,'digest','PREPARED',"
                            "5,'https://example.invalid/pull/5',0.0,NULL)"
                        ), {"base": "a"*40, "input": "b"*40, "head": "c"*40})
            finally:
                tx.rollback()
    finally:
        engine.dispose()
