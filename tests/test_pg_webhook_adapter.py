"""PostgreSQL webhook inbox contract and live transaction tests."""

import hmac
import http.client
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
from threading import Barrier, Event, Thread
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from agent_workflow.app_config import AppConfig
from agent_workflow.application.exceptions import (
    InvalidApplicationRequestError,
    PersistenceUnavailableError,
)
from agent_workflow.application.webhook.receive_webhook import ReceiveWebhookUseCase
from agent_workflow.composition import webhook_entrypoint
from agent_workflow.domain.ci_evidence import WebhookNotification
from agent_workflow.domain.webhook.inbox import InboxResult
from agent_workflow.infrastructure.database.repositories.webhook_inbox_repository import (
    SqlAlchemyWebhookInboxRepository,
)
from agent_workflow.infrastructure.database.session_factory import (
    PostgresFactory,
)


class _SessionInbox:
    """Test helper that exposes the repository's owned Session scope."""

    def __init__(self, factory):
        self._repository = SqlAlchemyWebhookInboxRepository(
            session_scope=factory.get_scoped_session
        )

    def record(self, notification, digest):
        return self._repository.record(notification, digest)

    def get(self, delivery_id):
        return self._repository.get(delivery_id)


def test_repository_owns_a_scoped_session_transaction():
    session = MagicMock(spec=Session)
    session.scalar.return_value = "00000000-0000-4000-8000-000000000001"
    scope = MagicMock()
    scope.__enter__.return_value = session
    session_scope = MagicMock(return_value=scope)
    notification = WebhookNotification(
        "00000000-0000-4000-8000-000000000001",
        "workflow_run",
        "completed",
        "owner/repo",
        3,
        4,
        "a" * 40,
    )
    repo = SqlAlchemyWebhookInboxRepository(session_scope=session_scope)
    assert repo.record(notification, "b" * 64) is InboxResult.INSERTED
    session_scope.assert_called_once_with()
    scope.__enter__.assert_called_once_with()
    session.begin.assert_called_once_with()
    scope.__exit__.assert_called_once_with(None, None, None)
    session.flush.assert_called_once()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_repository_get_uses_a_fresh_read_only_scope_without_commit():
    session = MagicMock(spec=Session)
    session.get.return_value = None
    scope = MagicMock()
    scope.__enter__.return_value = session
    session_scope = MagicMock(return_value=scope)
    repository = SqlAlchemyWebhookInboxRepository(session_scope=session_scope)

    assert repository.get("00000000-0000-4000-8000-000000000001") is None

    session_scope.assert_called_once_with()
    scope.__enter__.assert_called_once_with()
    scope.__exit__.assert_called_once_with(None, None, None)
    assert str(session.connection.return_value.execute.call_args_list[0].args[0]) == (
        "SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ ONLY"
    )
    session.begin.assert_not_called()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


def test_repository_commits_before_return_and_uses_fresh_sessions(config, delivery):
    engine = create_engine(config.pg_runtime_dsn, poolclass=NullPool)
    sessions = []
    settings = []

    @event.listens_for(engine, "before_cursor_execute")
    def capture_settings(conn, cursor, statement, parameters, context, executemany):
        if "SET TRANSACTION ISOLATION LEVEL READ COMMITTED" in statement:
            settings.append((statement, parameters))
        elif "set_config('lock_timeout'" in statement:
            settings.append((statement, parameters))

    with PostgresFactory(engine) as factory:
        open_scope = factory.get_scoped_session

        @contextmanager
        def capture_session():
            with open_scope() as session:
                sessions.append(session)
                yield session

        repository = SqlAlchemyWebhookInboxRepository(session_scope=capture_session)
        digest = _digest(delivery)
        assert repository.record(delivery, digest) is InboxResult.INSERTED

        # A separate Connection sees the row as soon as record() returns.
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT body_digest FROM webhook_inbox "
                        "WHERE delivery_id = :delivery_id"
                    ),
                    {"delivery_id": delivery.delivery_id},
                ).scalar_one()
                == digest
            )

        assert repository.get(delivery.delivery_id) == delivery

    assert len(sessions) == 2
    assert sessions[0] is not sessions[1]
    assert any(
        statement.endswith("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
        for statement, _ in settings
    )
    assert any(
        statement.endswith("SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ ONLY")
        for statement, _ in settings
    )
    set_config = next(
        params
        for statement, params in settings
        if "set_config('lock_timeout'" in statement
    )
    assert set_config == {
        "lock_timeout": "5s",
        "statement_timeout": "5s",
        "synchronous_commit": "on",
    }


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


def test_restart_replay(config, delivery):
    alternate = _alternate(delivery)
    first, second = _digest(delivery), _digest(delivery, "changed")
    with PostgresFactory.open(config) as factory:
        pg = _SessionInbox(factory)
        assert pg.get(delivery.delivery_id) is None
        assert pg.record(delivery, first) is InboxResult.INSERTED

    with PostgresFactory.open(config) as restarted:
        pg = _SessionInbox(restarted)
        assert pg.get(delivery.delivery_id) == delivery
        for notification, digest, expected in [
            (delivery, first, InboxResult.DUPLICATE),
            (delivery, second, InboxResult.CONFLICT),
            (alternate, first, InboxResult.DUPLICATE),
        ]:
            assert pg.record(notification, digest) is expected
        assert pg.get(alternate.delivery_id) is None
        assert pg.record(alternate, second) is InboxResult.INSERTED
        # Delivery ID conflict wins even when another delivery owns the digest.
        assert pg.record(delivery, second) is InboxResult.CONFLICT


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
        inbox = _SessionInbox(factory)
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
    use_case = ReceiveWebhookUseCase(_SessionInbox(factory))
    notification = WebhookNotification(
        str(uuid.uuid4()),
        "pull_request",
        "synchronize",
        "owner/repo",
        1,
        2,
        "a" * 40,
    )
    for invalid, digest in [
        (None, "b" * 64),
        (replace(notification, delivery_id=""), "b" * 64),
        (replace(notification, event="bad\\x00event"), "b" * 64),
        (replace(notification, installation_id=True), "b" * 64),
        (replace(notification, subject_id=2**63), "b" * 64),
        (notification, ""),
        (notification, b"digest"),
    ]:
        with pytest.raises(InvalidApplicationRequestError):
            use_case.execute(invalid, digest)
    factory.get_scoped_session.assert_not_called()


def test_commit_failure_rolls_back_and_read_failure_is_sanitized(config, delivery):
    engine = create_engine(config.pg_runtime_dsn, poolclass=NullPool)
    digest = _digest(delivery)
    with PostgresFactory(engine) as factory:
        inbox = _SessionInbox(factory)

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
            with pytest.raises(PersistenceUnavailableError) as exc:
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
                PersistenceUnavailableError,
                match="^PostgreSQL webhook inbox operation failed$",
            ):
                inbox.get(delivery.delivery_id)
        finally:
            event.remove(engine, "before_cursor_execute", fail_read)
        assert inbox.get(delivery.delivery_id) == delivery


def test_live_signed_http_pg_selection_and_replay(
    config, delivery, monkeypatch, tmp_path
):
    secret = b"z" * 32
    secret_path = tmp_path / "webhook-secret"
    secret_path.write_bytes(secret)
    monkeypatch.setenv("PG_RUNTIME_DSN", config.pg_runtime_dsn)
    monkeypatch.setenv("WFC_WEBHOOK_SECRET_FILE", str(secret_path))
    monkeypatch.setenv("WFC_CONTROLLER_INSTALLATION_ID", "3")
    monkeypatch.setenv("WFC_WEBHOOK_PORT", "0")
    monkeypatch.delenv("WFC_WEBHOOK_DB_PATH", raising=False)
    ready = Event()
    factories = []
    open_factory = PostgresFactory.open

    def capture_factory(app_config):
        factory = open_factory(app_config)
        factories.append(factory)
        return factory

    monkeypatch.setattr(webhook_entrypoint.PostgresFactory, "open", capture_factory)
    servers = []
    failures = []
    original_server = webhook_entrypoint.WebhookHTTPServer

    class ObservedServer(original_server):
        def serve_forever(self, poll_interval=0.5):
            ready.set()
            return super().serve_forever(poll_interval=poll_interval)

    monkeypatch.setattr(webhook_entrypoint, "WebhookHTTPServer", ObservedServer)

    def run():
        try:
            webhook_entrypoint.main()
        except Exception as error:
            failures.append(error)

    original_init = ObservedServer.__init__

    def capture_server(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        servers.append(self)

    monkeypatch.setattr(ObservedServer, "__init__", capture_server)
    thread = Thread(target=run, daemon=True)
    thread.start()
    try:
        assert ready.wait(10), failures
        port = servers[0].server_address[1]

        def post(body, delivery_id, signature=True):
            headers = {
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": delivery_id,
                "X-Hub-Signature-256": "sha256="
                + hmac.digest(secret, body, "sha256").hex(),
            }
            if not signature:
                headers.pop("X-Hub-Signature-256")
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                conn.request("POST", "/webhooks/github", body, headers)
                response = conn.getresponse()
                response.read()
                return response.status
            finally:
                conn.close()

        def body(installation=3, number=delivery.subject_id):
            return json.dumps(
                {
                    "action": "synchronize",
                    "repository": {"full_name": "builacviet/agent-workflow"},
                    "installation": {"id": installation},
                    "number": number,
                    "pull_request": {"head": {"sha": delivery.head_sha}},
                }
            ).encode()

        assert post(body(), delivery.delivery_id, signature=False) == 403
        assert post(body(installation=4), delivery.delivery_id) == 403
        with PostgresFactory.open(config) as check:
            assert _SessionInbox(check).get(delivery.delivery_id) is None

        def refuse_commit(connection):
            connection.execute(text("SELECT missing_webhook_commit_test_column"))

        engine = factories[0]._engine
        event.listen(engine, "commit", refuse_commit)
        try:
            assert post(body(), delivery.delivery_id) == 503
        finally:
            event.remove(engine, "commit", refuse_commit)
        with PostgresFactory.open(config) as check:
            assert _SessionInbox(check).get(delivery.delivery_id) is None

        assert post(body(), delivery.delivery_id) == 202
        with PostgresFactory.open(config) as check:
            persisted = _SessionInbox(check).get(delivery.delivery_id)
            assert persisted is not None
            assert persisted.subject_id == delivery.subject_id
        assert post(body(), delivery.delivery_id) == 200
        assert post(body(number=delivery.subject_id + 1), delivery.delivery_id) == 409
    finally:
        if servers:
            servers[0].shutdown()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert not failures
