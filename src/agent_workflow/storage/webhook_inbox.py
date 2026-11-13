"""Versioned, durable SQLite inbox for GitHub reconciliation notifications."""

import sqlite3
from contextlib import closing
from enum import Enum, auto
from pathlib import Path

from agent_workflow.domain.ci_evidence import WebhookNotification as WebhookNotification
from agent_workflow.storage.migrations import (
    Migration,
    migrate,
    table_names,
    validate_schema,
)
from agent_workflow.storage.workflow_migrations import LEGACY_WORKFLOW_TABLES

_LEGACY_ALIAS_SQL = """
    CREATE TABLE webhook_delivery_aliases (
        delivery_id TEXT PRIMARY KEY NOT NULL,
        body_digest TEXT NOT NULL
    )
"""


class InboxResult(Enum):
    INSERTED = auto()
    DUPLICATE = auto()
    CONFLICT = auto()


def _create_webhook(db: sqlite3.Connection) -> None:
    db.execute("""
        CREATE TABLE webhook_inbox (
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
    db.execute("""
        CREATE INDEX webhook_inbox_body_digest
        ON webhook_inbox (body_digest)
    """)


_WEBHOOK_MIGRATIONS = (Migration(1, "webhook_inbox", _create_webhook),)


def _validate_inbox_schema(db: sqlite3.Connection, version: int) -> None:
    with closing(sqlite3.connect(":memory:")) as expected:
        for step in _WEBHOOK_MIGRATIONS[:version]:
            step.upgrade(expected)
        validate_schema(
            db,
            expected,
            "webhook",
            retired_tables=("webhook_delivery_aliases",),
        )


def _legacy_webhook(db: sqlite3.Connection) -> int:
    names = table_names(db)
    if "webhook_inbox" not in names:
        if "webhook_delivery_aliases" in names:
            raise sqlite3.DatabaseError("orphaned legacy webhook aliases")
        # Without an inbox, accept a fresh file or a workflow database. A
        # registered workflow can have tables added by newer workflow migrations.
        workflow_registered = db.execute(
            "SELECT 1 FROM schema_migrations WHERE component = 'workflow' LIMIT 1"
        ).fetchone()
        if not workflow_registered and names - (
            LEGACY_WORKFLOW_TABLES | {"schema_migrations"}
        ):
            raise sqlite3.DatabaseError("unexpected legacy webhook tables")
        return 0

    db.execute("""
        CREATE INDEX IF NOT EXISTS webhook_inbox_body_digest
        ON webhook_inbox (body_digest)
    """)
    if "webhook_delivery_aliases" in names:
        with closing(sqlite3.connect(":memory:")) as expected:
            expected.execute(_LEGACY_ALIAS_SQL)
            validate_schema(db, expected, "legacy webhook alias")
        db.execute("DROP TABLE webhook_delivery_aliases")
    _validate_inbox_schema(db, 1)
    return 1


def _migrate_webhook(db: sqlite3.Connection) -> None:
    migrate(
        db,
        "webhook",
        _WEBHOOK_MIGRATIONS,
        adopt_legacy=_legacy_webhook,
        validate=_validate_inbox_schema,
    )


class WebhookInbox:
    """Store at most one notification per signed body, across restarts."""

    def __init__(self, path: str | Path) -> None:
        if str(path) in ("", ":memory:"):
            raise ValueError("webhook inbox requires a database file")
        self._path = Path(path).resolve()
        with closing(self._connect()) as db:
            _migrate_webhook(db)

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
        """Return only after committing; never store replay delivery aliases."""
        with closing(self._connect()) as db, db:
            # Serialize check-and-insert, including different IDs for the same
            # signed body. No duplicate produces another durable record.
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT body_digest FROM webhook_inbox WHERE delivery_id = ?",
                (notification.delivery_id,),
            ).fetchone()
            if existing is not None:
                return (
                    InboxResult.DUPLICATE
                    if existing[0] == body_digest
                    else InboxResult.CONFLICT
                )
            if (
                db.execute(
                    "SELECT 1 FROM webhook_inbox WHERE body_digest = ? LIMIT 1",
                    (body_digest,),
                ).fetchone()
                is not None
            ):
                return InboxResult.DUPLICATE

            # Also reject triggers installed after this adapter was opened.
            if (
                db.execute(
                    "SELECT 1 FROM main.sqlite_schema WHERE type='trigger' "
                    "AND tbl_name = 'webhook_inbox' COLLATE NOCASE LIMIT 1"
                ).fetchone()
                is not None
            ):
                raise sqlite3.DatabaseError("unexpected webhook inbox trigger")
            inserted = db.execute(
                """
                INSERT INTO webhook_inbox (
                    delivery_id, event, action, repository, installation_id,
                    subject_id, head_sha, body_digest
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    notification.delivery_id,
                    notification.event,
                    notification.action,
                    notification.repository,
                    notification.installation_id,
                    notification.subject_id,
                    notification.head_sha,
                    body_digest,
                ),
            )
            if inserted.rowcount != 1:
                raise sqlite3.DatabaseError("webhook notification was not inserted")
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
