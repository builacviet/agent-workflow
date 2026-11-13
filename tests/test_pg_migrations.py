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
    dsn = os.environ.get("PG_TEST_DSN")
    if not dsn:
        pytest.skip("disposable PG_TEST_DSN not supplied")
    url = sa.engine.make_url(dsn)
    if url.drivername != "postgresql+psycopg" or not url.database:
        pytest.fail("PG_TEST_DSN must be a PostgreSQL+psycopg test DSN")
    return sa.create_engine(dsn, pool_pre_ping=True)


def test_alembic_has_no_checked_in_database_credentials():
    root = Path(__file__).resolve().parents[1]
    ini = (root / "alembic.ini").read_text(encoding="utf-8")
    env = (root / "alembic" / "env.py").read_text(encoding="utf-8")
    migration = (root / "alembic" / "versions" / "pg01_baseline.py").read_text(
        encoding="utf-8"
    )
    assert "sqlalchemy.url =" not in ini
    assert "PG_MIGRATOR_DSN" in env
    assert "CREATE SCHEMA wfc" not in migration
    assert "public schema must be empty for baseline" in migration
    assert "Destructive PostgreSQL baseline downgrade is forbidden" in migration
    assert "from flask " not in env.lower()
    assert "import flask" not in env.lower()


def test_postgres_baseline_tables_and_version():
    engine = _test_engine()
    try:
        inspector = sa.inspect(engine)
        expected = {
            "tasks",
            "review_attempts",
            "reviews",
            "review_findings",
            "task_events",
            "agent_dispatches",
            "agent_execution_evidence",
            "webhook_inbox",
            "controller_ci_tasks",
            "controller_ci_observations",
            "controller_ci_processed",
            "publication_intents",
        }
        assert set(inspector.get_table_names(schema="public")) == expected | {
            "alembic_version"
        }
        assert "wfc" not in inspector.get_schema_names()
        assert "schema_migrations" not in inspector.get_table_names(schema="public")
        assert any(
            idx["name"] == "one_active_claim" and idx["unique"]
            for idx in inspector.get_indexes("review_attempts", schema="public")
        )
        assert any(
            idx["name"] == "webhook_inbox_body_digest"
            and idx["unique"]
            and idx["column_names"] == ["body_digest"]
            for idx in inspector.get_indexes("webhook_inbox", schema="public")
        )
        with engine.connect() as conn:
            assert (
                conn.execute(
                    sa.text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == "pg02d_webhook_digest"
            )
    finally:
        engine.dispose()


def test_webhook_digest_upgrade_rejects_duplicates_and_preserves_history():
    import uuid

    from alembic.config import Config
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[1]
    config = Config()
    config.set_main_option("script_location", str(root / "alembic"))
    revision = ScriptDirectory.from_config(config).get_revision("pg02d_webhook_digest")
    assert revision.down_revision == "pg02c_agent_dispatch"

    schema = "pg02d_" + uuid.uuid4().hex
    engine = _test_engine()
    assert engine.url.database in {"workflow_test", "workflow_ci"}
    try:
        with engine.connect() as conn, conn.begin() as transaction:
            conn.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
            conn.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
            conn.execute(
                sa.text(
                    "CREATE TABLE webhook_inbox (delivery_id TEXT PRIMARY KEY, "
                    "body_digest TEXT NOT NULL)"
                )
            )
            conn.execute(
                sa.text(
                    "CREATE INDEX webhook_inbox_body_digest ON webhook_inbox (body_digest)"
                )
            )
            conn.execute(
                sa.text(
                    "INSERT INTO webhook_inbox VALUES ('first', 'body'), ('second', 'body')"
                )
            )
            with pytest.raises(RuntimeError, match="duplicate body digests"):
                with (
                    conn.begin_nested(),
                    Operations.context(MigrationContext.configure(conn)),
                ):
                    revision.module.upgrade()
            assert conn.execute(
                sa.text(
                    "SELECT delivery_id, body_digest FROM webhook_inbox ORDER BY delivery_id"
                )
            ).all() == [("first", "body"), ("second", "body")]
            assert not sa.inspect(conn).get_indexes("webhook_inbox", schema=schema)[0][
                "unique"
            ]

            conn.execute(
                sa.text(
                    "UPDATE webhook_inbox SET body_digest='different' WHERE delivery_id='second'"
                )
            )
            with Operations.context(MigrationContext.configure(conn)):
                revision.module.upgrade()
            assert conn.execute(
                sa.text(
                    "SELECT delivery_id, body_digest FROM webhook_inbox ORDER BY delivery_id"
                )
            ).all() == [("first", "body"), ("second", "different")]
            with pytest.raises(sa.exc.IntegrityError):
                with conn.begin_nested():
                    conn.execute(
                        sa.text("INSERT INTO webhook_inbox VALUES ('third', 'body')")
                    )
            transaction.rollback()
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
                conn.execute(
                    sa.text(
                        "INSERT INTO tasks(task_id,metadata,repository,branch,base_sha,"
                        "requested_effort,require_verified_effort,status,created_at,updated_at) "
                        "VALUES ('PG01_SLOT_TEST','{}','owner/repo','codex/example',:sha,"
                        "'high',1,'AWAITING_HANDOFF',0.0,0.0)"
                    ),
                    {"sha": "a" * 40},
                )
                conn.execute(
                    sa.text(
                        "INSERT INTO agent_dispatches "
                        "(dispatch_id,task_id,round_number,input_sha,status,slot,deadline,role,phase,subject_type,subject_id,subject_version) "
                        "VALUES ('PG01_DISPATCH_A','PG01_SLOT_TEST',1,:sha,'ACTIVE',1,100.0,'dev','code','task','PG01_SLOT_TEST','1:' || :sha)"
                    ),
                    {"sha": "b" * 40},
                )
                with pytest.raises(IntegrityError):
                    with conn.begin_nested():
                        conn.execute(
                            sa.text(
                                "INSERT INTO agent_dispatches "
                                "(dispatch_id,task_id,round_number,input_sha,status,slot,deadline,role,phase,subject_type,subject_id,subject_version) "
                                "VALUES ('PG01_DISPATCH_B','PG01_SLOT_TEST',2,:sha,'BLOCKED',1,100.0,'dev','code','task','PG01_SLOT_TEST','2:' || :sha)"
                            ),
                            {"sha": "c" * 40},
                        )
                assert (
                    conn.execute(
                        sa.text(
                            "SELECT COUNT(*) FROM agent_dispatches "
                            "WHERE dispatch_id = 'PG01_DISPATCH_A'"
                        )
                    ).scalar_one()
                    == 1
                )
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
                conn.execute(
                    sa.text(
                        "INSERT INTO tasks(task_id,metadata,repository,branch,base_sha,"
                        "requested_effort,require_verified_effort,status,created_at,updated_at) "
                        "VALUES ('PG01_PUBLISH_TEST','{}','owner/repo','codex/example',:sha,"
                        "'high',1,'AWAITING_HANDOFF',0.0,0.0)"
                    ),
                    {"sha": "a" * 40},
                )
                conn.execute(
                    sa.text(
                        "INSERT INTO agent_dispatches "
                        "(dispatch_id,task_id,round_number,input_sha,status,slot,deadline,role,phase,subject_type,subject_id,subject_version) "
                        "VALUES ('PG01_PUBLISH_DISPATCH','PG01_PUBLISH_TEST',1,:sha,"
                        "'BLOCKED',1,100.0,'dev','code','task','PG01_PUBLISH_TEST','1:' || :sha)"
                    ),
                    {"sha": "b" * 40},
                )
                with pytest.raises(IntegrityError):
                    with conn.begin_nested():
                        conn.execute(
                            sa.text(
                                "INSERT INTO publication_intents "
                                "(dispatch_id,task_id,repository,branch,base_sha,input_sha,"
                                "expected_head_sha,tested_digest,state,pr_number,pr_url,"
                                "created_at,verified_at) VALUES "
                                "('PG01_PUBLISH_DISPATCH','PG01_PUBLISH_TEST','owner/repo',"
                                "'codex/example',:base,:input,:head,'digest','PREPARED',"
                                "5,'https://example.invalid/pull/5',0.0,NULL)"
                            ),
                            {"base": "a" * 40, "input": "b" * 40, "head": "c" * 40},
                        )
            finally:
                tx.rollback()
    finally:
        engine.dispose()


@pytest.mark.parametrize("mismatch", [False, True])
def test_nonempty_dispatch_upgrade_preserves_evidence_and_publication_fk(
    mismatch, monkeypatch
):
    """Exercise the actual revision in a transaction-local isolated schema."""
    import importlib.util
    import uuid

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = _test_engine()
    revision_path = (
        Path(__file__).resolve().parents[1] / "alembic/versions/pg02c_agent_dispatch.py"
    )
    revision_spec = importlib.util.spec_from_file_location(
        "pg02c_test_revision", revision_path
    )
    revision = importlib.util.module_from_spec(revision_spec)
    revision_spec.loader.exec_module(revision)
    baseline_path = (
        Path(__file__).resolve().parents[1] / "alembic/versions/pg01_baseline.py"
    )
    baseline_spec = importlib.util.spec_from_file_location(
        "pg01_test_revision", baseline_path
    )
    baseline = importlib.util.module_from_spec(baseline_spec)
    baseline_spec.loader.exec_module(baseline)
    schema = "pg02c_" + uuid.uuid4().hex
    assert sa.engine.make_url(os.environ["PG_TEST_DSN"]).database in {
        "workflow_test",
        "workflow_ci",
    }
    try:
        with engine.connect() as conn:
            transaction = conn.begin()
            try:
                # The identifier is a locally generated UUID, never user input.
                conn.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
                conn.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))

                # pg01 checks public explicitly. The scratch schema is new and empty;
                # override only that precheck while running the actual baseline DDL.
                class EmptySchemaCheck:
                    def execute(self, statement):
                        assert "information_schema.tables" in str(statement)
                        return self

                    def scalar_one_or_none(self):
                        return None

                with Operations.context(MigrationContext.configure(conn)):
                    with monkeypatch.context() as patch:
                        patch.setattr(
                            baseline.op, "get_bind", lambda: EmptySchemaCheck()
                        )
                        baseline.upgrade()
                conn.execute(
                    sa.text(
                        "INSERT INTO tasks(task_id,metadata,repository,branch,base_sha,requested_effort,require_verified_effort,status,created_at,updated_at) VALUES ('TASK','{}','owner/repo','branch',:sha,'high',0,'AWAITING_HANDOFF',0,0)"
                    ),
                    {"sha": "a" * 40},
                )
                conn.execute(
                    sa.text(
                        "INSERT INTO coder_dispatches VALUES ('dispatch','TASK',1,:input,'DONE',NULL,123,:head,'')"
                    ),
                    {"input": "a" * 40, "head": "b" * 40},
                )
                conn.execute(
                    sa.text(
                        "INSERT INTO coder_runtime_evidence VALUES ('dispatch','TASK',:input,'codex','v1',NULL,'UNVERIFIED',:digest,:head,42)"
                    ),
                    {"input": "a" * 40, "digest": "c" * 64, "head": "b" * 40},
                )
                conn.execute(
                    sa.text(
                        "INSERT INTO publication_intents(dispatch_id,task_id,repository,branch,base_sha,input_sha,expected_head_sha,tested_digest,state,created_at) VALUES ('dispatch','TASK','owner/repo','branch',:base,:input,:head,'digest','PREPARED',0)"
                    ),
                    {"base": "a" * 40, "input": "a" * 40, "head": "b" * 40},
                )
                if mismatch:
                    conn.execute(
                        sa.text("UPDATE coder_runtime_evidence SET input_sha='wrong'")
                    )
                    with pytest.raises(RuntimeError, match="incompatible historical"):
                        with Operations.context(MigrationContext.configure(conn)):
                            revision.upgrade()
                    return
                with Operations.context(MigrationContext.configure(conn)):
                    revision.upgrade()
                row = conn.execute(
                    sa.text(
                        "SELECT role,phase,subject_type,subject_id,subject_version,result_sha FROM agent_dispatches WHERE dispatch_id='dispatch'"
                    )
                ).one()
                assert tuple(row) == (
                    "dev",
                    "code",
                    "task",
                    "TASK",
                    "1:" + "a" * 40,
                    "b" * 40,
                )
                evidence = conn.execute(
                    sa.text(
                        "SELECT evidence_kind,trusted_writer,task_id,input_sha,published_head_sha FROM agent_execution_evidence WHERE dispatch_id='dispatch'"
                    )
                ).one()
                assert tuple(evidence) == (
                    "dev_runtime",
                    "host",
                    "TASK",
                    "a" * 40,
                    "b" * 40,
                )
                assert (
                    conn.execute(
                        sa.text(
                            "SELECT count(*) FROM publication_intents p JOIN agent_dispatches d USING(dispatch_id)"
                        )
                    ).scalar_one()
                    == 1
                )
                with pytest.raises(sa.exc.IntegrityError):
                    with conn.begin_nested():
                        conn.execute(
                            sa.text(
                                "INSERT INTO publication_intents(dispatch_id,task_id,repository,branch,base_sha,input_sha,expected_head_sha,tested_digest,state,created_at) VALUES ('missing','TASK','owner/repo','branch','base','input','head','digest','PREPARED',0)"
                            )
                        )
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
