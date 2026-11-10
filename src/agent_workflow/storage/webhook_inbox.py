"""Durable, file-backed inbox for GitHub reconciliation notifications."""
from contextlib import closing
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
import sqlite3


@dataclass(frozen=True, slots=True)
class WebhookNotification:
    delivery_id: str
    event: str
    action: str
    repository: str
    installation_id: int
    subject_id: int
    head_sha: str


class InboxResult(Enum):
    INSERTED = auto()
    DUPLICATE = auto()
    CONFLICT = auto()


class WebhookInbox:
    """Keep the first notification for a delivery ID across receiver restarts."""

    def __init__(self, path: str | Path) -> None:
        if str(path) in ("", ":memory:"):
            raise ValueError("webhook inbox requires a database file")
        self._path = Path(path).resolve()
        with closing(self._connect()) as db, db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS webhook_inbox (
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

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._path)
        try:
            db.execute("PRAGMA synchronous = FULL")
        except sqlite3.Error:
            db.close()
            raise
        return db

    def record(
        self, notification: WebhookNotification, body_digest: str
    ) -> InboxResult:
        """Return after commit; the primary key arbitrates concurrent writers."""
        with closing(self._connect()) as db, db:
            inserted = db.execute(
                """
                INSERT INTO webhook_inbox (
                    delivery_id, event, action, repository, installation_id,
                    subject_id, head_sha, body_digest
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(delivery_id) DO NOTHING
                """,
                (
                    notification.delivery_id, notification.event,
                    notification.action, notification.repository,
                    notification.installation_id, notification.subject_id,
                    notification.head_sha, body_digest,
                ),
            )
            if inserted.rowcount == 1:
                return InboxResult.INSERTED
            existing = db.execute(
                "SELECT event, body_digest FROM webhook_inbox WHERE delivery_id = ?",
                (notification.delivery_id,),
            ).fetchone()
            if existing == (notification.event, body_digest):
                return InboxResult.DUPLICATE
            return InboxResult.CONFLICT

    def get(self, delivery_id: str) -> WebhookNotification | None:
        with closing(self._connect()) as db:
            row = db.execute(
                """
                SELECT delivery_id, event, action, repository, installation_id,
                       subject_id, head_sha
                FROM webhook_inbox WHERE delivery_id = ?
                """,
                (delivery_id,),
            ).fetchone()
        return WebhookNotification(*row) if row is not None else None
