"""Exercise signed receiver admission and outcome mapping through its current port."""

import hashlib
import hmac
import json
from http import HTTPStatus
from unittest import TestCase
from unittest.mock import Mock

from agent_workflow.application.webhook.receive_webhook import ReceiveWebhookUseCase
from agent_workflow.domain.ci_evidence import WebhookNotification
from agent_workflow.domain.webhook.inbox import InboxResult
from agent_workflow.domain.workflow_identity import REPOSITORY
from agent_workflow.interfaces.webhook.github_receiver import (
    WEBHOOK_PATH,
    GitHubWebhookReceiver,
)

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
        "X-Hub-Signature-256": "sha256="
        + hmac.new(secret, body, hashlib.sha256).hexdigest(),
    }


class GitHubWebhookTests(TestCase):
    def setUp(self) -> None:
        self.inbox = Mock()
        self.inbox.record.return_value = InboxResult.INSERTED
        self.receiver = self.make_receiver()
        self.body = encode(payload_for("workflow_run"))

    def make_receiver(self, **kwargs: int) -> GitHubWebhookReceiver:
        return GitHubWebhookReceiver(
            use_case=ReceiveWebhookUseCase(inbox=self.inbox),
            secret=SECRET,
            installation_id=INSTALLATION_ID,
            **kwargs,
        )

    def test_accepts_both_events_as_notifications(self) -> None:
        for event, action, delivery, subject_id in (
            ("workflow_run", "completed", DELIVERY_ID, 456),
            ("pull_request", "synchronize", OTHER_DELIVERY_ID, 42),
        ):
            with self.subTest(event=event):
                body = encode(payload_for(event))
                headers = signed_headers(body, event, delivery=delivery)
                lowercase_headers = {
                    key.lower(): value for key, value in headers.items()
                }
                self.assertEqual(
                    self.receiver.handle("POST", WEBHOOK_PATH, lowercase_headers, body),
                    HTTPStatus.ACCEPTED,
                )
                self.inbox.record.assert_called_with(
                    WebhookNotification(
                        delivery,
                        event,
                        action,
                        REPOSITORY,
                        INSTALLATION_ID,
                        subject_id,
                        HEAD_SHA,
                    ),
                    hashlib.sha256(body).hexdigest(),
                )

    def test_tampering_wrong_secret_and_bad_signatures(self) -> None:
        for body, headers in (
            (self.body + b" ", signed_headers(self.body)),
            (self.body, signed_headers(self.body, secret=b"wrong-secret")),
            (self.body, {}),
            (
                self.body,
                {**signed_headers(self.body), "X-Hub-Signature-256": "sha1=abc"},
            ),
            (
                self.body,
                {**signed_headers(self.body), "X-Hub-Signature-256": "sha256=é"},
            ),
        ):
            with self.subTest(headers=headers):
                self.assertEqual(
                    self.receiver.handle("POST", WEBHOOK_PATH, headers, body),
                    HTTPStatus.FORBIDDEN,
                )
        self.inbox.record.assert_not_called()

    def test_normalizes_delivery_and_maps_duplicate_and_conflict(self) -> None:
        headers = signed_headers(self.body, delivery=DELIVERY_ID.upper())
        for outcome, status in (
            (InboxResult.DUPLICATE, HTTPStatus.OK),
            (InboxResult.CONFLICT, HTTPStatus.CONFLICT),
        ):
            self.inbox.record.return_value = outcome
            self.assertEqual(
                self.receiver.handle("POST", WEBHOOK_PATH, headers, self.body), status
            )
            self.inbox.record.assert_called_with(
                WebhookNotification(
                    DELIVERY_ID,
                    "workflow_run",
                    "completed",
                    REPOSITORY,
                    INSTALLATION_ID,
                    456,
                    HEAD_SHA,
                ),
                hashlib.sha256(self.body).hexdigest(),
            )

    def test_only_post_to_exact_path_and_bounded_body(self) -> None:
        receiver = self.make_receiver(max_body_bytes=len(self.body))
        for method, path, body, expected in (
            ("GET", WEBHOOK_PATH, self.body, HTTPStatus.METHOD_NOT_ALLOWED),
            ("POST", "/mcp", self.body, HTTPStatus.NOT_FOUND),
            ("POST", WEBHOOK_PATH + "/", self.body, HTTPStatus.NOT_FOUND),
            (
                "POST",
                WEBHOOK_PATH,
                self.body + b" ",
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            ),
            ("POST", WEBHOOK_PATH, b"", HTTPStatus.BAD_REQUEST),
        ):
            with self.subTest(method=method, path=path, size=len(body)):
                self.assertEqual(
                    receiver.handle(method, path, signed_headers(body), body), expected
                )
        self.inbox.record.assert_not_called()
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
                    self.receiver.handle(
                        "POST", WEBHOOK_PATH, signed_headers(body), body
                    ),
                    HTTPStatus.FORBIDDEN,
                )
        self.inbox.record.assert_not_called()

    def test_rejects_malformed_signed_payloads(self) -> None:
        for body in (
            b"{",
            b"[]",
            b"\xff",
            b'{"a": 1, "a": 2}',
            b'{"a": NaN}',
            b"[" * 2000 + b"]" * 2000,
            encode({**payload_for("workflow_run"), "workflow_run": None}),
            encode({**payload_for("workflow_run"), "workflow_run": {"id": 456}}),
            encode(
                {
                    **payload_for("workflow_run"),
                    "workflow_run": {"id": 2**63, "head_sha": HEAD_SHA},
                }
            ),
            encode(
                {
                    **payload_for("pull_request"),
                    "action": "synchronize",
                    "workflow_run": {"id": 456, "head_sha": HEAD_SHA},
                }
            ),
        ):
            with self.subTest(body=body[:80]):
                self.assertEqual(
                    self.receiver.handle(
                        "POST", WEBHOOK_PATH, signed_headers(body), body
                    ),
                    HTTPStatus.BAD_REQUEST,
                )
        self.inbox.record.assert_not_called()

    def test_signed_irrelevant_actions_acknowledge_without_queuing(self) -> None:
        examples = [
            (
                "workflow_run",
                encode({**payload_for("workflow_run"), "action": "requested"}),
            ),
            (
                "pull_request",
                encode({**payload_for("pull_request"), "action": "opened"}),
            ),
            (
                "pull_request",
                encode({**payload_for("pull_request"), "action": "closed"}),
            ),
            ("push", encode(payload_for("workflow_run"))),
            ("ping", encode({"zen": "Keep it logically awesome!", "hook_id": 33})),
        ]
        for event, body in examples:
            with self.subTest(event=event):
                self.assertEqual(
                    self.receiver.handle(
                        "POST", WEBHOOK_PATH, signed_headers(body, event), body
                    ),
                    HTTPStatus.OK,
                )
        signed_bad_identity = encode(
            {
                **payload_for("workflow_run"),
                "action": "requested",
                "installation": {"id": INSTALLATION_ID + 1},
            }
        )
        self.assertEqual(
            self.receiver.handle(
                "POST",
                WEBHOOK_PATH,
                signed_headers(signed_bad_identity),
                signed_bad_identity,
            ),
            HTTPStatus.FORBIDDEN,
        )
        self.assertEqual(
            self.receiver.handle(
                "POST",
                WEBHOOK_PATH,
                signed_headers(self.body, secret=b"bad"),
                self.body,
            ),
            HTTPStatus.FORBIDDEN,
        )
        self.inbox.record.assert_not_called()

    def test_rejects_malformed_metadata_and_ambiguous_headers(self) -> None:
        headers = signed_headers(self.body)
        missing_delivery = {
            key: value for key, value in headers.items() if key != "X-GitHub-Delivery"
        }
        for bad_headers in (
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
        self.inbox.record.assert_not_called()
