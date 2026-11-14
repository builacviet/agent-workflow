"""PostgreSQL webhook inbox contract and live transaction tests."""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
from threading import Barrier
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from agent_workflow.app_config import AppConfig
from agent_workflow.storage.postgres_connection import (
    PostgresFactory,
    PostgresUnavailableError,
)
from agent_workflow.storage.postgres_webhook import PostgresWebhookInbox
from agent_workflow.storage.webhook_inbox import (
    InboxResult,
    WebhookInbox,
    WebhookNotification,
)


@pytest.fixture
def config():
    dsn = os.environ.get("PG_TEST_DSN")
    if not dsn:
        pytest.skip("disposable PG_TEST_DSN not supplied")
    if make_url(dsn).database not in {"workflow_test", "workflow_ci"}:
        pytest.fail("webhook tests require workflow_test or workflow_ci")
    return AppConfig(pg_runtime_dsn=dsn)


@pytest.fixture
def delivery(config):
    notification = WebhookNotification(
        "a" + str(uuid.uuid4())[1:],
        "workflow_run",
        "completed",
        "owner/repo",
        3,
        2**32,
        "a" * 40,
    )
    try:
        yield notification
    finally:
        with PostgresFactory.open(config) as factory, factory.transaction() as conn:
            conn.execute(
                text(
                    "DELETE FROM webhook_inbox WHERE delivery_id IN (:first, :second)"
                ),
                {
                    "first": notification.delivery_id,
                    "second": _alternate(notification).delivery_id,
                },
            )


def _alternate(notification):
    return replace(notification, delivery_id=notification.delivery_id.upper())


def _digest(notification, suffix=""):
    return sha256((notification.delivery_id + suffix).encode()).hexdigest()


def test_restart_replay_matches_sqlite(config, delivery, tmp_path):
    alternate = _alternate(delivery)
    first, second = _digest(delivery), _digest(delivery, "changed")
    sqlite = WebhookInbox(tmp_path / "inbox.sqlite")
    with PostgresFactory.open(config) as factory:
        pg = PostgresWebhookInbox(factory)
        assert pg.get(delivery.delivery_id) is None
        assert (
            pg.record(delivery, first)
            is sqlite.record(delivery, first)
            is InboxResult.INSERTED
        )

    with PostgresFactory.open(config) as restarted:
        pg = PostgresWebhookInbox(restarted)
        assert (
            pg.get(delivery.delivery_id) == sqlite.get(delivery.delivery_id) == delivery
        )
        for notification, digest, expected in [
            (delivery, first, InboxResult.DUPLICATE),
            (delivery, second, InboxResult.CONFLICT),
            (alternate, first, InboxResult.DUPLICATE),
        ]:
            assert (
                pg.record(notification, digest)
                is sqlite.record(notification, digest)
                is expected
            )
        assert (
            pg.get(alternate.delivery_id) is sqlite.get(alternate.delivery_id) is None
        )
        assert (
            pg.record(alternate, second)
            is sqlite.record(alternate, second)
            is InboxResult.INSERTED
        )
        # Delivery ID conflict wins even when another delivery owns the supplied digest.
        assert (
            pg.record(delivery, second)
            is sqlite.record(delivery, second)
            is InboxResult.CONFLICT
        )


@pytest.mark.parametrize(
    ("same_id", "same_digest", "other_result"),
    [
        (True, True, InboxResult.DUPLICATE),
        (True, False, InboxResult.CONFLICT),
        (False, True, InboxResult.DUPLICATE),
    ],
)
def test_concurrent_collision(config, delivery, same_id, same_digest, other_result):
    engine = create_engine(config.pg_runtime_dsn, poolclass=NullPool)
    both_writing = Barrier(2)
    pids = []

    @event.listens_for(engine, "before_cursor_execute")
    def rendezvous(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO webhook_inbox"):
            pids.append(cursor.connection.info.backend_pid)
            both_writing.wait(timeout=5)

    alternate = delivery if same_id else _alternate(delivery)
    first = _digest(delivery)
    second = first if same_digest else _digest(delivery, "changed")
    with PostgresFactory(engine) as factory:
        inbox = PostgresWebhookInbox(factory)
        with ThreadPoolExecutor(max_workers=2) as pool:
            requests = [
                pool.submit(inbox.record, delivery, first),
                pool.submit(inbox.record, alternate, second),
            ]
            outcomes = [request.result(timeout=15) for request in requests]
        assert len(set(pids)) == 2
        assert sorted(outcome.name for outcome in outcomes) == sorted(
            [InboxResult.INSERTED.name, other_result.name]
        )
        winner = (delivery, alternate)[outcomes.index(InboxResult.INSERTED)]
        assert inbox.get(winner.delivery_id) == winner
        if not same_id:
            loser = (delivery, alternate)[outcomes.index(other_result)]
            assert inbox.get(loser.delivery_id) is None


def test_invalid_input_is_rejected_before_connecting():
    factory = MagicMock(spec=PostgresFactory)
    inbox = PostgresWebhookInbox(factory)
    notification = WebhookNotification(
        "delivery", "pull_request", "synchronize", "owner/repo", 1, 2, "a" * 40
    )
    for invalid, digest in [
        (None, "digest"),
        (replace(notification, delivery_id=""), "digest"),
        (replace(notification, event="bad\x00event"), "digest"),
        (replace(notification, installation_id=True), "digest"),
        (replace(notification, subject_id=2**63), "digest"),
        (notification, ""),
        (notification, b"digest"),
    ]:
        with pytest.raises(ValueError):
            inbox.record(invalid, digest)
    with pytest.raises(ValueError):
        inbox.get(None)
    factory.repository_session.assert_not_called()


def test_commit_failure_rolls_back_and_read_failure_is_sanitized(config, delivery):
    engine = create_engine(config.pg_runtime_dsn, poolclass=NullPool)
    digest = _digest(delivery)
    with PostgresFactory(engine) as factory:
        inbox = PostgresWebhookInbox(factory)

        def fail_commit(conn):
            # Confirm the write reached PostgreSQL before aborting its transaction.
            assert (
                conn.execute(
                    text(
                        "SELECT body_digest FROM webhook_inbox WHERE delivery_id = :delivery_id"
                    ),
                    {"delivery_id": delivery.delivery_id},
                ).scalar_one()
                == digest
            )
            conn.execute(text("SELECT missing_commit_test_column"))

        event.listen(engine, "commit", fail_commit)
        try:
            with pytest.raises(PostgresUnavailableError) as exc:
                inbox.record(delivery, digest)
            assert str(exc.value) == "PostgreSQL webhook inbox operation failed"
            assert exc.value.__suppress_context__
        finally:
            event.remove(engine, "commit", fail_commit)
        assert inbox.get(delivery.delivery_id) is None
        assert inbox.record(delivery, digest) is InboxResult.INSERTED

        event.listen(engine, "commit", fail_commit)
        try:
            assert inbox.get(delivery.delivery_id) == delivery
        finally:
            event.remove(engine, "commit", fail_commit)

        def fail_read(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith("SELECT webhook_inbox."):
                return "SELECT missing_read_test_column", {}
            return statement, parameters

        event.listen(engine, "before_cursor_execute", fail_read, retval=True)
        try:
            with pytest.raises(
                PostgresUnavailableError,
                match="^PostgreSQL webhook inbox operation failed$",
            ):
                inbox.get(delivery.delivery_id)
        finally:
            event.remove(engine, "before_cursor_execute", fail_read)
        assert inbox.get(delivery.delivery_id) == delivery
