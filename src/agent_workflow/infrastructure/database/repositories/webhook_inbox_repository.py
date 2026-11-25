"""PostgreSQL webhook inbox persistence with Repository-owned transactions."""

from collections.abc import Callable
from contextlib import AbstractContextManager

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from agent_workflow.application.exceptions import PersistenceUnavailableError
from agent_workflow.domain.ci_evidence import WebhookNotification
from agent_workflow.domain.webhook.inbox import InboxResult
from agent_workflow.infrastructure.database.models import WebhookInboxRow
from agent_workflow.infrastructure.database.unit_of_work import (
    _set_repository_transaction_options,
)

_OPERATION_ERROR = "PostgreSQL webhook inbox operation failed"
SessionScope = Callable[[], AbstractContextManager[Session]]


class SqlAlchemyWebhookInboxRepository:
    """Own a short transaction for each Webhook inbox operation."""

    def __init__(self, session_scope: SessionScope) -> None:
        self._session_scope = session_scope

    def record(
        self, notification: WebhookNotification, body_digest: str
    ) -> InboxResult:
        """Commit an insertion or roll back an unchanged inbox before returning."""
        try:
            with self._session_scope() as session:
                with session.begin() as transaction:
                    _set_repository_transaction_options(
                        session.connection(), read_only=False
                    )
                    result = self._record(session, notification, body_digest)
                    if result is not InboxResult.INSERTED:
                        transaction.rollback()
            return result
        except SQLAlchemyError:
            raise PersistenceUnavailableError(_OPERATION_ERROR) from None

    def _record(
        self,
        session: Session,
        notification: WebhookNotification,
        body_digest: str,
    ) -> InboxResult:
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
        session.flush()
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
            raise PersistenceUnavailableError(
                "PostgreSQL webhook inbox insert was not confirmed"
            )
        if (
            existing.delivery_id == notification.delivery_id
            and existing.body_digest != body_digest
        ):
            return InboxResult.CONFLICT
        return InboxResult.DUPLICATE
