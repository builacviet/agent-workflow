"""PostgreSQL webhook inbox persistence with Repository-owned transactions."""

from collections.abc import Callable
from contextlib import AbstractContextManager

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from agent_workflow.domain.ci_evidence import WebhookNotification
from agent_workflow.domain.webhook.inbox import InboxResult
from agent_workflow.infrastructure.database.models import WebhookInboxRow
from agent_workflow.infrastructure.database.unit_of_work import PostgresUnavailableError

_OPERATION_ERROR = "PostgreSQL webhook inbox operation failed"
SessionScope = Callable[[], AbstractContextManager[Session]]


def _set_transaction_options(session: Session, *, read_only: bool) -> None:
    mode = ", READ ONLY" if read_only else ""
    connection = session.connection()
    connection.execute(text(f"SET TRANSACTION ISOLATION LEVEL READ COMMITTED{mode}"))
    connection.execute(
        text(
            "SELECT set_config('lock_timeout', :lock_timeout, true), "
            "set_config('statement_timeout', :statement_timeout, true), "
            "set_config('synchronous_commit', :synchronous_commit, true)"
        ),
        {
            "lock_timeout": "5s",
            "statement_timeout": "5s",
            "synchronous_commit": "on",
        },
    )


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
    """Own a short transaction for each Webhook inbox operation."""

    def __init__(self, session_scope: SessionScope) -> None:
        self._session_scope = session_scope

    def record(
        self, notification: WebhookNotification, body_digest: str
    ) -> InboxResult:
        """Commit the inbox operation before returning its outcome."""
        try:
            with self._session_scope() as session:
                with session.begin():
                    _set_transaction_options(session, read_only=False)
                    result = self._record(session, notification, body_digest)
            return result
        except (SQLAlchemyError, ValidationError):
            raise PostgresUnavailableError(_OPERATION_ERROR) from None

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
        """Read in a fresh read-only scope without committing a transaction."""
        try:
            with self._session_scope() as session:
                _set_transaction_options(session, read_only=True)
                row = session.get(WebhookInboxRow, delivery_id)
                if row is None:
                    result = None
                else:
                    dto = _NotificationDTO.model_validate(row)
                    result = WebhookNotification(**dto.model_dump())
            return result
        except (SQLAlchemyError, ValidationError):
            raise PostgresUnavailableError(_OPERATION_ERROR) from None
