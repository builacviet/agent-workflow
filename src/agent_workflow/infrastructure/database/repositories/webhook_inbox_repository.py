"""PostgreSQL webhook inbox persistence using a caller-owned SQLAlchemy Session."""

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from agent_workflow.domain.ci_evidence import WebhookNotification
from agent_workflow.domain.webhook.inbox import InboxResult
from agent_workflow.infrastructure.database.models import WebhookInboxRow
from agent_workflow.infrastructure.database.unit_of_work import PostgresUnavailableError


class _NotificationDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True, strict=True)

    delivery_id: str
    event: str
    action: str
    repository: str
    installation_id: int
    subject_id: int
    head_sha: str


class SqlAlchemyWebhookInboxRepository:
    """Execute inbox queries in the transaction owned by the caller."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self, notification: WebhookNotification, body_digest: str
    ) -> InboxResult:
        """Return a result; the caller must commit before HTTP acknowledges it."""
        row = WebhookInboxRow
        inbox = row.__table__
        inserted_id = self._session.scalar(
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
        self._session.flush()
        if inserted_id is not None:
            return InboxResult.INSERTED

        same_delivery = row.delivery_id == notification.delivery_id
        existing = self._session.execute(
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
        row = self._session.get(WebhookInboxRow, delivery_id)
        if row is None:
            return None
        dto = _NotificationDTO.model_validate(row)
        return WebhookNotification(**dto.model_dump())
