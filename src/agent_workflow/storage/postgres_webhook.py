"""PostgreSQL-backed signed webhook notification inbox.

PostgreSQL UNIQUE constraints replace SQLite's whole-file BEGIN IMMEDIATE;
the adapter never makes authorization or CI-verdict decisions.
"""
from __future__ import annotations

from agent_workflow.storage.webhook_inbox import InboxResult, WebhookNotification

from psycopg import Error as PostgresError


class PostgresWebhookInbox:
    def __init__(self, dsn: str):
        if not isinstance(dsn, str) or not dsn.startswith(("postgresql://", "postgres://")):
            raise ValueError("PostgreSQL DSN required")
        self.dsn = dsn

    def _connect(self):
        import psycopg
        return psycopg.connect(self.dsn, autocommit=True, connect_timeout=8)

    def record(self, notification: WebhookNotification, body_digest: str) -> InboxResult:
        if type(notification) is not WebhookNotification:
            raise TypeError("untrusted webhook notification")
        if not isinstance(body_digest, str) or len(body_digest) != 64:
            raise ValueError("invalid webhook body digest")
        try:
            return self._record_transaction(notification, body_digest)
        except PostgresError:
            # Webhook HTTP maps OSError to 503, not 202. Do not log a DSN.
            raise OSError("PostgreSQL webhook inbox unavailable") from None

    def _record_transaction(self, notification: WebhookNotification, body_digest: str) -> InboxResult:
        with self._connect() as conn:
            with conn.transaction():
                inserted = conn.execute(
                    "INSERT INTO workflow.webhook_inbox "
                    "(delivery_id,event,action,repository,installation_id,subject_id,head_sha,body_digest) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT DO NOTHING RETURNING delivery_id",
                    (
                        notification.delivery_id, notification.event,
                        notification.action, notification.repository,
                        notification.installation_id, notification.subject_id,
                        notification.head_sha, body_digest,
                    ),
                ).fetchone()
                if inserted:
                    return InboxResult.INSERTED
                existing = conn.execute(
                    "SELECT body_digest FROM workflow.webhook_inbox WHERE delivery_id=%s",
                    (notification.delivery_id,),
                ).fetchone()
                if existing:
                    return InboxResult.DUPLICATE if existing[0] == body_digest else InboxResult.CONFLICT
                if conn.execute(
                    "SELECT 1 FROM workflow.webhook_inbox WHERE body_digest=%s",
                    (body_digest,),
                ).fetchone():
                    return InboxResult.DUPLICATE
                # An unexpected trigger or schema incompatibility must not
                # silently acknowledge a missing notification.
                raise RuntimeError("webhook notification was not inserted")

    def get(self, delivery_id: str) -> WebhookNotification | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT delivery_id,event,action,repository,installation_id,subject_id,head_sha "
                "FROM workflow.webhook_inbox WHERE delivery_id=%s", (delivery_id,),
            ).fetchone()
            return WebhookNotification(*row) if row else None
