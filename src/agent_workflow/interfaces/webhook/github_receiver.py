"""Signed GitHub webhook validation and inbound application adapter."""

import hashlib
import hmac
import json
import re
from collections.abc import Iterable, Mapping
from http import HTTPStatus

from agent_workflow.application.webhook.ports import IWebhookInboxRepository
from agent_workflow.application.webhook.receive_webhook import ReceiveWebhookUseCase
from agent_workflow.domain.ci_evidence import WebhookNotification
from agent_workflow.domain.webhook.inbox import InboxResult
from agent_workflow.domain.workflow_identity import (
    REPOSITORY,
    WorkflowError,
    validate_sha,
)

WEBHOOK_PATH = "/webhooks/github"
DEFAULT_MAX_BODY_BYTES = 1024 * 1024
_ACTIONS = {"workflow_run": "completed", "pull_request": "synchronize"}
_VALID_ACTIONS = {
    "workflow_run": frozenset({"requested", "in_progress", "completed"}),
    "pull_request": frozenset(
        {
            "assigned",
            "auto_merge_disabled",
            "auto_merge_enabled",
            "closed",
            "converted_to_draft",
            "demilestoned",
            "dequeued",
            "edited",
            "enqueued",
            "labeled",
            "locked",
            "milestoned",
            "opened",
            "ready_for_review",
            "reopened",
            "review_request_removed",
            "review_requested",
            "synchronize",
            "unassigned",
            "unlabeled",
            "unlocked",
        }
    ),
}
_SIGNATURE = re.compile(r"sha256=[0-9a-f]{64}")
_DELIVERY = re.compile(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", re.I)
_WEBHOOK_HEADERS = frozenset(
    {
        "x-hub-signature-256",
        "x-github-event",
        "x-github-delivery",
    }
)


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
    # GitHub IDs must fit the signed 64-bit PostgreSQL/SQLite integer range.
    return type(value) is int and 0 < value < 2**63


class GitHubWebhookReceiver:
    """Validate signed HTTP input, then pass a typed receipt to Application."""

    def __init__(
        self,
        inbox: IWebhookInboxRepository | ReceiveWebhookUseCase,
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
        self._use_case = (
            inbox
            if isinstance(inbox, ReceiveWebhookUseCase)
            else ReceiveWebhookUseCase(inbox)
        )
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
        if event == "ping":
            return HTTPStatus.OK

        repository, installation = (
            payload.get("repository"),
            payload.get("installation"),
        )
        if (
            not isinstance(repository, dict)
            or repository.get("full_name") != self._repository
            or not isinstance(installation, dict)
            or not _positive_id(installation.get("id"))
            or installation["id"] != self._installation_id
        ):
            return HTTPStatus.FORBIDDEN

        # Valid signed event types outside the subscribed wake-up set are no-ops.
        if event not in _ACTIONS:
            return HTTPStatus.OK
        action = payload.get("action")
        if not isinstance(action, str) or not action:
            return HTTPStatus.BAD_REQUEST
        subject = payload.get(event)
        if not isinstance(subject, dict) or action not in _VALID_ACTIONS[event]:
            return HTTPStatus.BAD_REQUEST
        if action != _ACTIONS[event]:
            return HTTPStatus.OK

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
            delivery.lower(),
            event,
            action,
            self._repository,
            self._installation_id,
            subject_id,
            head_sha,
        )
        result = self._use_case.execute(notification, hashlib.sha256(body).hexdigest())
        if result is InboxResult.INSERTED:
            return HTTPStatus.ACCEPTED
        if result is InboxResult.DUPLICATE:
            return HTTPStatus.OK
        return HTTPStatus.CONFLICT
