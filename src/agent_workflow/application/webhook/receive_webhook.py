"""Application boundary for a validated webhook notification."""

import re

from agent_workflow.application.exceptions import (
    ApplicationError,
    InvalidApplicationRequestError,
)
from agent_workflow.application.webhook.ports import IWebhookInboxRepository
from agent_workflow.domain.ci_evidence import WebhookNotification
from agent_workflow.domain.exceptions import DomainError
from agent_workflow.domain.webhook.inbox import InboxResult
from agent_workflow.domain.workflow_identity import validate_sha

_DELIVERY = re.compile(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}")
_REPOSITORY = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+")


class ReceiveWebhookUseCase:
    """Validate direct-call inputs before asking the inbox to persist them."""

    def __init__(self, inbox: IWebhookInboxRepository) -> None:
        if not callable(getattr(inbox, "record", None)):
            raise ApplicationError("webhook inbox repository is required")
        self._inbox = inbox

    def execute(
        self, notification: WebhookNotification, body_digest: str
    ) -> InboxResult:
        if type(notification) is not WebhookNotification:
            raise InvalidApplicationRequestError("invalid webhook notification")
        if (
            not isinstance(notification.delivery_id, str)
            or not _DELIVERY.fullmatch(notification.delivery_id)
            or not isinstance(notification.event, str)
            or notification.event not in {"workflow_run", "pull_request"}
            or not isinstance(notification.action, str)
            or notification.action
            != {"workflow_run": "completed", "pull_request": "synchronize"}.get(
                notification.event
            )
            or not isinstance(notification.repository, str)
            or not _REPOSITORY.fullmatch(notification.repository)
            or type(notification.installation_id) is not int
            or not 0 < notification.installation_id < 2**63
            or type(notification.subject_id) is not int
            or not 0 < notification.subject_id < 2**63
        ):
            raise InvalidApplicationRequestError("invalid webhook notification")
        try:
            validate_sha(notification.head_sha)
        except DomainError:
            raise InvalidApplicationRequestError(
                "invalid webhook notification"
            ) from None
        if not isinstance(body_digest, str) or not re.fullmatch(
            r"[0-9a-f]{64}", body_digest
        ):
            raise InvalidApplicationRequestError("invalid webhook body digest")

        result = self._inbox.record(notification, body_digest)
        if type(result) is not InboxResult:
            raise ApplicationError("invalid webhook inbox result")
        return result
