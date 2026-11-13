"""Opt-in PostgreSQL storage for validated webhook notifications."""

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from agent_workflow.storage.postgres_connection import (
    PostgresFactory,
    PostgresUnavailableError,
)
from agent_workflow.storage.postgres_models import WebhookInboxRow
from agent_workflow.storage.webhook_inbox import InboxResult, WebhookNotification

_OPERATION_ERROR = "PostgreSQL webhook inbox operation failed"


class _NotificationDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True, strict=True)

    delivery_id: str
    event: str
    action: str
    repository: str
    installation_id: int
    subject_id: int
    head_sha: str


def _validate_text(value: object) -> None:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("invalid webhook inbox text")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("invalid webhook inbox text") from None


class PostgresWebhookInbox:
    """Persist notifications through a caller-owned PostgreSQL factory."""

    def __init__(self, factory: PostgresFactory) -> None:
        self._factory = factory

    def record(
        self, notification: WebhookNotification, body_digest: str
    ) -> InboxResult:
        """Commit before returning and never store a replay delivery alias."""
        if not isinstance(notification, WebhookNotification):
            raise ValueError("invalid webhook notification")
        for value in (
            notification.delivery_id,
            notification.event,
            notification.action,
            notification.repository,
            notification.head_sha,
            body_digest,
        ):
            _validate_text(value)
        for value in (notification.installation_id, notification.subject_id):
            if type(value) is not int or not -(2**63) <= value < 2**63:
                raise ValueError("invalid webhook notification numeric ID")

        with self._factory.repository_session(
            error_message=_OPERATION_ERROR
        ) as session:
            row = WebhookInboxRow
            inbox = row.__table__
            inserted_id = session.scalar(
                insert(inbox)
                .values(
                    delivery_id=notification.delivery_id,
                    event=notification.event,
                    action=notification.action,
                    repository=notification.repository,
                    installation_id=notification.installation_id,
                    subject_id=notification.subject_id,
                    head_sha=notification.head_sha,
                    body_digest=body_digest,
                )
                .on_conflict_do_nothing()
                .returning(inbox.c.delivery_id)
            )
            if inserted_id is not None:
                return InboxResult.INSERTED

            same_delivery = row.delivery_id == notification.delivery_id
            existing = session.execute(
                select(row.delivery_id, row.body_digest)
                .where(same_delivery | (row.body_digest == body_digest))
                .order_by(same_delivery.desc())
                .limit(1)
            ).one_or_none()
            if existing is None:
                raise PostgresUnavailableError(
                    "PostgreSQL webhook inbox insert was not confirmed"
                )
            if (
                existing.delivery_id == notification.delivery_id
                and existing.body_digest != body_digest
            ):
                return InboxResult.CONFLICT
            return InboxResult.DUPLICATE

    def get(self, delivery_id: str) -> WebhookNotification | None:
        _validate_text(delivery_id)
        with self._factory.repository_session(
            error_message=_OPERATION_ERROR
        ) as session:
            row = session.get(WebhookInboxRow, delivery_id)
            if row is None:
                return None
            dto = _NotificationDTO.model_validate(row)
            return WebhookNotification(**dto.model_dump())
