"""Offline HTTP request handling for GitHub App reconciliation signals."""
from collections.abc import Iterable, Mapping
import hashlib
import hmac
from http import HTTPStatus
import json
import re
import sqlite3

from agent_workflow.integrations.github import REPOSITORY, WorkflowError, validate_sha
from agent_workflow.storage.webhook_inbox import (
    InboxResult,
    WebhookInbox,
    WebhookNotification,
)


WEBHOOK_PATH = "/webhooks/github"
DEFAULT_MAX_BODY_BYTES = 1024 * 1024
_ACTIONS = {"workflow_run": "completed", "pull_request": "synchronize"}
_SIGNATURE = re.compile(r"sha256=[0-9a-f]{64}")
_DELIVERY = re.compile(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", re.I)
_WEBHOOK_HEADERS = frozenset({
    "x-hub-signature-256", "x-github-event", "x-github-delivery",
})


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(_: str) -> None:
    raise ValueError("invalid JSON constant")


def _positive_id(value: object) -> bool:
    # GitHub IDs must fit SQLite INTEGER columns. In Python bool is an int subclass.
    return type(value) is int and 0 < value < 2**63


class GitHubWebhookReceiver:
    """Validate a buffered request and record a wake-up hint for reconciliation.

    A transport must cap bytes while reading and pass the original body and header
    pairs. The receiver opens no sockets and performs no GitHub API calls.
    """

    def __init__(
        self,
        inbox: WebhookInbox,
        *,
        secret: bytes,
        installation_id: int,
        repository: str = REPOSITORY,
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    ) -> None:
        if not isinstance(secret, bytes) or not secret:
            raise ValueError("a nonempty webhook secret is required")
        if not _positive_id(installation_id):
            raise ValueError("invalid GitHub installation ID")
        if not isinstance(repository, str) or not re.fullmatch(
            r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repository
        ):
            raise ValueError("GitHub repository must be an owner/name slug")
        if type(max_body_bytes) is not int or max_body_bytes <= 0:
            raise ValueError("body limit must be a positive integer")
        self._inbox = inbox
        self._secret = secret
        self._installation_id = installation_id
        self._repository = repository
        self._max_body_bytes = max_body_bytes

    def handle(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str] | Iterable[tuple[str, str]],
        body: bytes,
    ) -> HTTPStatus:
        if path != WEBHOOK_PATH:
            return HTTPStatus.NOT_FOUND
        if method != "POST":
            return HTTPStatus.METHOD_NOT_ALLOWED
        if not isinstance(body, bytes) or not body:
            return HTTPStatus.BAD_REQUEST
        if len(body) > self._max_body_bytes:
            return HTTPStatus.REQUEST_ENTITY_TOO_LARGE

        header_pairs = headers.items() if isinstance(headers, Mapping) else headers
        selected: dict[str, str] = {}
        for name, value in header_pairs:
            name = name.lower()
            if name in _WEBHOOK_HEADERS:
                if name in selected:
                    return HTTPStatus.BAD_REQUEST
                selected[name] = value.strip(" \t")

        signature = selected.get("x-hub-signature-256", "")
        if not _SIGNATURE.fullmatch(signature):
            return HTTPStatus.FORBIDDEN
        expected = "sha256=" + hmac.digest(self._secret, body, "sha256").hex()
        if not hmac.compare_digest(signature, expected):
            return HTTPStatus.FORBIDDEN

        event = selected.get("x-github-event", "")
        delivery = selected.get("x-github-delivery", "")
        if not event or not _DELIVERY.fullmatch(delivery):
            return HTTPStatus.BAD_REQUEST
        try:
            payload = json.loads(
                body.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=_invalid_constant,
            )
        except (ValueError, RecursionError):
            return HTTPStatus.BAD_REQUEST
        if not isinstance(payload, dict):
            return HTTPStatus.BAD_REQUEST

        # GitHub App ping is signed but has no task identity or action.
        # Acknowledging it never authorizes CI or mutates the inbox.
        if event == "ping":
            return HTTPStatus.OK

        repository, installation = payload.get("repository"), payload.get("installation")
        if (
            not isinstance(repository, dict)
            or repository.get("full_name") != self._repository
            or not isinstance(installation, dict)
            or not _positive_id(installation.get("id"))
            or installation["id"] != self._installation_id
        ):
            return HTTPStatus.FORBIDDEN

        # Registered GitHub App event subscriptions deliver many actions.
        # Only the two wake-up signals are queued; all other valid signed
        # events/actions are acknowledged without writing or deciding CI.
        if event not in _ACTIONS:
            return HTTPStatus.OK
        action = payload.get("action")
        if not isinstance(action, str) or not action:
            return HTTPStatus.BAD_REQUEST
        # Even a no-op action must belong to the signed event type;
        # a mislabeled body must not be silently acknowledged.
        subject = payload.get(event)
        if not isinstance(subject, dict):
            return HTTPStatus.BAD_REQUEST
        if action != _ACTIONS[event]:
            return HTTPStatus.OK

        if not isinstance(subject, dict):
            return HTTPStatus.BAD_REQUEST
        if event == "workflow_run":
            subject_id, head_sha = subject.get("id"), subject.get("head_sha")
        else:
            subject_id, head = payload.get("number"), subject.get("head")
            head_sha = head.get("sha") if isinstance(head, dict) else None
        if not _positive_id(subject_id):
            return HTTPStatus.BAD_REQUEST
        try:
            validate_sha(head_sha)
        except WorkflowError:
            return HTTPStatus.BAD_REQUEST

        notification = WebhookNotification(
            delivery.lower(), event, action, self._repository,
            self._installation_id, subject_id, head_sha,
        )
        try:
            result = self._inbox.record(notification, hashlib.sha256(body).hexdigest())
        except sqlite3.Error:
            return HTTPStatus.SERVICE_UNAVAILABLE
        if result is InboxResult.INSERTED:
            return HTTPStatus.ACCEPTED
        if result is InboxResult.DUPLICATE:
            return HTTPStatus.OK
        return HTTPStatus.CONFLICT
