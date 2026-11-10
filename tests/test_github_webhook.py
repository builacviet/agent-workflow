"""Exercise the offline receiver with signed requests and a disposable SQLite file."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import hmac
from http import HTTPStatus
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest import TestCase
from unittest.mock import patch

from agent_workflow.integrations.github import REPOSITORY
from agent_workflow.integrations.github_webhook import WEBHOOK_PATH, GitHubWebhookReceiver
from agent_workflow.storage.webhook_inbox import WebhookInbox, WebhookNotification


SECRET = b"offline-test-secret"
INSTALLATION_ID = 123
DELIVERY_ID = "0b989ba4-242f-11e5-81e1-c7b6966d2516"
OTHER_DELIVERY_ID = "b9957ec5-c3d7-4564-b6b7-241d6435b09a"
HEAD_SHA = "a" * 40


def payload_for(event: str) -> dict:
    payload = {
        "repository": {"full_name": REPOSITORY},
        "installation": {"id": INSTALLATION_ID},
        "sender": {"login": "tést"},
    }
    if event == "workflow_run":
        payload.update(
            action="completed",
            workflow_run={"id": 456, "head_sha": HEAD_SHA, "conclusion": "success"},
        )
    else:
        payload.update(
            action="synchronize",
            number=42,
            pull_request={"head": {"sha": HEAD_SHA}},
        )
    return payload


def encode(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def signed_headers(
    body: bytes,
    event: str = "workflow_run",
    *,
    delivery: str = DELIVERY_ID,
    secret: bytes = SECRET,
) -> dict[str, str]:
    return {
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery,
        "X-Hub-Signature-256": "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest(),
    }


class GitHubWebhookTests(TestCase):
    def setUp(self) -> None:
        tmp = self.enterContext(TemporaryDirectory())
        self.path = Path(tmp) / "webhook.sqlite3"
        self.inbox = WebhookInbox(self.path)
        self.receiver = self.make_receiver(self.inbox)
        self.body = encode(payload_for("workflow_run"))

    def make_receiver(self, inbox: WebhookInbox, **kwargs: int) -> GitHubWebhookReceiver:
        return GitHubWebhookReceiver(
            inbox, secret=SECRET, installation_id=INSTALLATION_ID, **kwargs
        )

    def test_accepts_both_events_as_notifications(self) -> None:
        for event, action, delivery, subject_id in (
            ("workflow_run", "completed", DELIVERY_ID, 456),
            ("pull_request", "synchronize", OTHER_DELIVERY_ID, 42),
        ):
            with self.subTest(event=event):
                body = encode(payload_for(event))
                headers = signed_headers(body, event, delivery=delivery)
                lowercase_headers = {key.lower(): value for key, value in headers.items()}
                self.assertEqual(
                    self.receiver.handle("POST", WEBHOOK_PATH, lowercase_headers, body),
                    HTTPStatus.ACCEPTED,
                )
                self.assertEqual(
                    self.inbox.get(delivery),
                    WebhookNotification(
                        delivery, event, action, REPOSITORY, INSTALLATION_ID,
                        subject_id, HEAD_SHA,
                    ),
                )

    def test_tampering_wrong_secret_and_bad_signatures(self) -> None:
        for body, headers in (
            (self.body + b" ", signed_headers(self.body)),
            (self.body, signed_headers(self.body, secret=b"wrong-secret")),
            (self.body, {}),
            (self.body, {**signed_headers(self.body), "X-Hub-Signature-256": "sha1=abc"}),
            (self.body, {**signed_headers(self.body), "X-Hub-Signature-256": "sha256=é"}),
        ):
            with self.subTest(headers=headers):
                self.assertEqual(
                    self.receiver.handle("POST", WEBHOOK_PATH, headers, body),
                    HTTPStatus.FORBIDDEN,
                )
        self.assertIsNone(self.inbox.get(DELIVERY_ID))

    def test_replay_after_restart_keeps_original_notification(self) -> None:
        headers = signed_headers(self.body)
        self.assertEqual(
            self.receiver.handle("POST", WEBHOOK_PATH, headers, self.body),
            HTTPStatus.ACCEPTED,
        )
        original = self.inbox.get(DELIVERY_ID)
        self.assertIsNotNone(original)
        reopened = WebhookInbox(self.path)
        receiver = self.make_receiver(reopened)
        self.assertEqual(reopened.get(DELIVERY_ID), original)
        replay = signed_headers(self.body, delivery=DELIVERY_ID.upper())
        self.assertEqual(
            receiver.handle("POST", WEBHOOK_PATH, replay, self.body), HTTPStatus.OK
        )
        self.assertEqual(reopened.get(DELIVERY_ID), original)

    def test_same_id_with_different_body_is_rejected(self) -> None:
        headers = signed_headers(self.body)
        self.assertEqual(
            self.receiver.handle("POST", WEBHOOK_PATH, headers, self.body),
            HTTPStatus.ACCEPTED,
        )
        payload = payload_for("workflow_run")
        payload["workflow_run"]["id"] = 789
        other_body = encode(payload)
        self.assertEqual(
            self.receiver.handle("POST", WEBHOOK_PATH, headers, other_body),
            HTTPStatus.FORBIDDEN,
        )
        self.assertEqual(
            self.receiver.handle("POST", WEBHOOK_PATH, signed_headers(other_body), other_body),
            HTTPStatus.CONFLICT,
        )
        self.assertEqual(self.inbox.get(DELIVERY_ID).subject_id, 456)

    def test_replay_under_new_id_stays_a_duplicate_after_restart(self) -> None:
        self.assertEqual(
            self.receiver.handle("POST", WEBHOOK_PATH, signed_headers(self.body), self.body),
            HTTPStatus.ACCEPTED,
        )
        replay = {**signed_headers(self.body), "X-GitHub-Delivery": OTHER_DELIVERY_ID}
        self.assertEqual(
            self.receiver.handle("POST", WEBHOOK_PATH, replay, self.body), HTTPStatus.OK
        )
        reopened = WebhookInbox(self.path)
        receiver = self.make_receiver(reopened)
        self.assertIsNone(reopened.get(OTHER_DELIVERY_ID))
        self.assertEqual(
            receiver.handle("POST", WEBHOOK_PATH, replay, self.body), HTTPStatus.OK
        )

        # Even a distinct signed body that would otherwise be accepted cannot
        # rebind an acknowledged replay UUID.
        other_body = self.body + b" "
        headers = signed_headers(other_body, delivery=OTHER_DELIVERY_ID)
        self.assertEqual(
            receiver.handle("POST", WEBHOOK_PATH, headers, other_body), HTTPStatus.CONFLICT
        )
        new_id = "704aaefb-66af-46a4-b1cf-6d8d37a28bad"
        headers["X-GitHub-Delivery"] = new_id
        self.assertEqual(
            receiver.handle("POST", WEBHOOK_PATH, headers, other_body), HTTPStatus.ACCEPTED
        )
        self.assertIsNotNone(reopened.get(new_id))

    def test_concurrent_duplicate_deliveries_insert_once(self) -> None:
        for second_id in (DELIVERY_ID, OTHER_DELIVERY_ID):
            with self.subTest(second_id=second_id):
                path = self.path.with_name(second_id + ".sqlite3")
                inbox = WebhookInbox(path)
                first = self.make_receiver(inbox)
                second = self.make_receiver(WebhookInbox(path))
                barrier = Barrier(2)

                def deliver(receiver: GitHubWebhookReceiver, delivery: str) -> HTTPStatus:
                    headers = signed_headers(self.body, delivery=delivery)
                    barrier.wait(timeout=10)
                    return receiver.handle("POST", WEBHOOK_PATH, headers, self.body)

                with ThreadPoolExecutor(max_workers=2) as workers:
                    jobs = (
                        workers.submit(deliver, first, DELIVERY_ID),
                        workers.submit(deliver, second, second_id),
                    )
                    results = [job.result() for job in jobs]
                self.assertCountEqual(results, (HTTPStatus.ACCEPTED, HTTPStatus.OK))
                with closing(sqlite3.connect(path)) as db:
                    self.assertEqual(db.execute("SELECT count(*) FROM webhook_inbox").fetchone(), (1,))

    def test_upgrades_legacy_inbox_without_dropping_existing_replays(self) -> None:
        path = self.path.with_name("legacy.sqlite3")
        digest = hashlib.sha256(self.body).hexdigest()
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("""
                CREATE TABLE webhook_inbox (
                    delivery_id TEXT PRIMARY KEY NOT NULL,
                    event TEXT NOT NULL,
                    action TEXT NOT NULL,
                    repository TEXT NOT NULL,
                    installation_id INTEGER NOT NULL,
                    subject_id INTEGER NOT NULL,
                    head_sha TEXT NOT NULL,
                    body_digest TEXT NOT NULL
                )
            """)
            db.executemany(
                "INSERT INTO webhook_inbox VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (delivery_id, "workflow_run", "completed", REPOSITORY,
                     INSTALLATION_ID, 456, HEAD_SHA, digest)
                    for delivery_id in (DELIVERY_ID, OTHER_DELIVERY_ID)
                ],
            )
            legacy_rows = db.execute("SELECT * FROM webhook_inbox ORDER BY delivery_id").fetchall()

        receiver = self.make_receiver(WebhookInbox(path))
        new_id = "704aaefb-66af-46a4-b1cf-6d8d37a28bad"
        replay = signed_headers(self.body, delivery=new_id)
        self.assertEqual(
            receiver.handle("POST", WEBHOOK_PATH, replay, self.body), HTTPStatus.OK
        )
        other_body = self.body + b" "
        headers = signed_headers(other_body, delivery=OTHER_DELIVERY_ID)
        self.assertEqual(
            receiver.handle("POST", WEBHOOK_PATH, headers, other_body), HTTPStatus.CONFLICT
        )
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(
                db.execute("SELECT * FROM webhook_inbox ORDER BY delivery_id").fetchall(),
                legacy_rows,
            )

    def test_failed_commit_never_acknowledges_and_allows_retry(self) -> None:
        connect = self.inbox._connect

        def deny_commit(action: int, argument: str, *_: object) -> int:
            if action == sqlite3.SQLITE_TRANSACTION and argument == "COMMIT":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        def connect_without_commit() -> sqlite3.Connection:
            db = connect()
            db.set_authorizer(deny_commit)
            return db

        headers = signed_headers(self.body)
        with patch.object(self.inbox, "_connect", side_effect=connect_without_commit):
            self.assertEqual(
                self.receiver.handle("POST", WEBHOOK_PATH, headers, self.body),
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
        self.assertIsNone(self.inbox.get(DELIVERY_ID))
        self.assertEqual(
            self.receiver.handle("POST", WEBHOOK_PATH, headers, self.body),
            HTTPStatus.ACCEPTED,
        )

    def test_only_post_to_exact_path_and_bounded_body(self) -> None:
        receiver = self.make_receiver(self.inbox, max_body_bytes=len(self.body))
        for method, path, body, expected in (
            ("GET", WEBHOOK_PATH, self.body, HTTPStatus.METHOD_NOT_ALLOWED),
            ("POST", "/mcp", self.body, HTTPStatus.NOT_FOUND),
            ("POST", WEBHOOK_PATH + "/", self.body, HTTPStatus.NOT_FOUND),
            ("POST", WEBHOOK_PATH, self.body + b" ", HTTPStatus.REQUEST_ENTITY_TOO_LARGE),
            ("POST", WEBHOOK_PATH, b"", HTTPStatus.BAD_REQUEST),
        ):
            with self.subTest(method=method, path=path, size=len(body)):
                self.assertEqual(receiver.handle(method, path, signed_headers(body), body), expected)
        self.assertIsNone(self.inbox.get(DELIVERY_ID))
        self.assertEqual(
            receiver.handle("POST", WEBHOOK_PATH, signed_headers(self.body), self.body),
            HTTPStatus.ACCEPTED,
        )

    def test_rejects_wrong_or_missing_identity(self) -> None:
        for change in (
            {"repository": {"full_name": "another/repository"}},
            {"repository": None},
            {"installation": None},
            {"installation": {"id": INSTALLATION_ID + 1}},
            {"installation": {"id": str(INSTALLATION_ID)}},
            {"installation": {"id": float(INSTALLATION_ID)}},
            {"installation": {"id": True}},
        ):
            with self.subTest(change=change):
                body = encode({**payload_for("workflow_run"), **change})
                self.assertEqual(
                    self.receiver.handle("POST", WEBHOOK_PATH, signed_headers(body), body),
                    HTTPStatus.FORBIDDEN,
                )
        self.assertIsNone(self.inbox.get(DELIVERY_ID))

    def test_rejects_malformed_signed_payloads(self) -> None:
        for body in (
            b"{", b"[]", b"\xff", b'{"a": 1, "a": 2}', b'{"a": NaN}',
            b"[" * 2000 + b"]" * 2000,
            encode({**payload_for("workflow_run"), "action": "requested"}),
            encode({**payload_for("workflow_run"), "workflow_run": None}),
            encode({**payload_for("workflow_run"), "workflow_run": {"id": 456}}),
            encode({
                **payload_for("workflow_run"),
                "workflow_run": {"id": 2**63, "head_sha": HEAD_SHA},
            }),
            encode({**payload_for("pull_request"), "action": "synchronize",
                    "workflow_run": {"id": 456, "head_sha": HEAD_SHA}}),
        ):
            with self.subTest(body=body[:80]):
                self.assertEqual(
                    self.receiver.handle("POST", WEBHOOK_PATH, signed_headers(body), body),
                    HTTPStatus.BAD_REQUEST,
                )
        self.assertIsNone(self.inbox.get(DELIVERY_ID))

    def test_rejects_unsupported_events_and_ambiguous_headers(self) -> None:
        headers = signed_headers(self.body)
        missing_delivery = {key: value for key, value in headers.items()
                            if key != "X-GitHub-Delivery"}
        for bad_headers in (
            signed_headers(self.body, "push"),
            signed_headers(self.body, "pull_request"),
            signed_headers(self.body, delivery="invalid-id"),
            missing_delivery,
            [*headers.items(), ("x-github-delivery", DELIVERY_ID)],
            [*headers.items(), ("x-hub-signature-256", headers["X-Hub-Signature-256"])],
            [*headers.items(), ("x-github-event", "workflow_run")],
        ):
            with self.subTest(headers=bad_headers):
                self.assertEqual(
                    self.receiver.handle("POST", WEBHOOK_PATH, bad_headers, self.body),
                    HTTPStatus.BAD_REQUEST,
                )
        self.assertIsNone(self.inbox.get(DELIVERY_ID))
