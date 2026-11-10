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
    """Keep the first notification for each signed body across restarts."""

    def __init__(self, path: str | Path) -> None:
        if str(path) in ("", ":memory:"):
            raise ValueError("webhook inbox requires a database file")
        self._path = Path(path).resolve()
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
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

            # Upgrade in place. A non-unique index preserves all legacy rows,
            # including any duplicates already recorded under different IDs.
            db.execute("""
                CREATE INDEX IF NOT EXISTS webhook_inbox_body_digest
                ON webhook_inbox (body_digest)
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS webhook_delivery_aliases (
                    delivery_id TEXT PRIMARY KEY NOT NULL,
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
        """Bind the delivery ID and acknowledge only after the transaction commits."""
        with closing(self._connect()) as db, db:
            # Acquire the write lock before either lookup so distinct delivery
            # IDs for the same body cannot both insert a notification.
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                """
                SELECT body_digest FROM webhook_inbox WHERE delivery_id = ?
                UNION ALL
                SELECT body_digest FROM webhook_delivery_aliases WHERE delivery_id = ?
                """,
                (notification.delivery_id, notification.delivery_id),
            ).fetchone()
            if existing is not None:
                if existing[0] != body_digest:
                    return InboxResult.CONFLICT
                return InboxResult.DUPLICATE

            if db.execute(
                "SELECT 1 FROM webhook_inbox WHERE body_digest = ? LIMIT 1",
                (body_digest,),
            ).fetchone() is not None:
                # Remember the replay's UUID so it cannot be rebound to a
                # different body. An alias never queues another notification.
                db.execute(
                    "INSERT INTO webhook_delivery_aliases VALUES (?, ?)",
                    (notification.delivery_id, body_digest),
                )
                return InboxResult.DUPLICATE

            db.execute(
                """
                INSERT INTO webhook_inbox (
                    delivery_id, event, action, repository, installation_id,
                    subject_id, head_sha, body_digest
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    notification.delivery_id, notification.event,
                    notification.action, notification.repository,
                    notification.installation_id, notification.subject_id,
                    notification.head_sha, body_digest,
                ),
            )
            return InboxResult.INSERTED

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
