"""Versioned, durable SQLite inbox for GitHub reconciliation notifications."""
from contextlib import closing
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
import sqlite3


_SCHEMA_VERSION = 1
_INBOX_COLUMNS = (
    ("delivery_id", "TEXT", 1, 1),
    ("event", "TEXT", 1, 0),
    ("action", "TEXT", 1, 0),
    ("repository", "TEXT", 1, 0),
    ("installation_id", "INTEGER", 1, 0),
    ("subject_id", "INTEGER", 1, 0),
    ("head_sha", "TEXT", 1, 0),
    ("body_digest", "TEXT", 1, 0),
)
_ALIAS_COLUMNS = (
    ("delivery_id", "TEXT", 1, 1),
    ("body_digest", "TEXT", 1, 0),
)


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


def _table_columns(db: sqlite3.Connection, table: str) -> tuple:
    # Only call with hardcoded table names, never external input.
    return tuple(
        (row[1], row[2].upper(), row[3], row[5])
        for row in db.execute(f"PRAGMA table_info({table})")
    )


def _validate_inbox_schema(db: sqlite3.Connection) -> None:
    if _table_columns(db, "webhook_inbox") != _INBOX_COLUMNS:
        raise sqlite3.DatabaseError("incompatible webhook inbox table schema")
    indexes = db.execute("PRAGMA index_list(webhook_inbox)").fetchall()
    digest_indexes = [row for row in indexes if row[1] == "webhook_inbox_body_digest"]
    if (
        len(digest_indexes) != 1
        or digest_indexes[0][2] != 0
        or digest_indexes[0][4] != 0
        or tuple(row[2] for row in db.execute(
            "PRAGMA index_info(webhook_inbox_body_digest)"
        )) != ("body_digest",)
    ):
        raise sqlite3.DatabaseError("incompatible webhook inbox digest index")


class WebhookInbox:
    """Store at most one notification per signed body, across restarts."""

    def __init__(self, path: str | Path) -> None:
        if str(path) in ("", ":memory:"):
            raise ValueError("webhook inbox requires a database file")
        self._path = Path(path).resolve()
        with closing(self._connect()) as db, db:
            # Migration and validation share one write transaction; a failure
            # rolls back both schema changes and the version bump.
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, _SCHEMA_VERSION):
                raise sqlite3.DatabaseError("unsupported webhook inbox schema version")
            tables = {
                row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            if tables - {"webhook_inbox", "webhook_delivery_aliases"}:
                raise sqlite3.DatabaseError("unexpected tables in webhook inbox database")

            if version == 0:
                if "webhook_inbox" in tables:
                    # A previous unversioned receiver must have the expected
                    # columns before we migrate it, never silently repair it.
                    if _table_columns(db, "webhook_inbox") != _INBOX_COLUMNS:
                        raise sqlite3.DatabaseError("incompatible legacy inbox schema")
                else:
                    if "webhook_delivery_aliases" in tables:
                        raise sqlite3.DatabaseError("orphaned legacy webhook aliases")
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
                if "webhook_delivery_aliases" in tables:
                    if _table_columns(db, "webhook_delivery_aliases") != _ALIAS_COLUMNS:
                        raise sqlite3.DatabaseError("incompatible legacy alias schema")
                    # Deliberately retire replay aliases. Only original
                    # notification IDs remain bound; old inbox rows are kept.
                    db.execute("DROP TABLE webhook_delivery_aliases")
                db.execute("""
                    CREATE INDEX IF NOT EXISTS webhook_inbox_body_digest
                    ON webhook_inbox (body_digest)
                """)
                db.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            elif "webhook_delivery_aliases" in tables:
                raise sqlite3.DatabaseError("unexpected aliases in current inbox schema")
            _validate_inbox_schema(db)

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
                    if existing[0] == body_digest else InboxResult.CONFLICT
                )
            if db.execute(
                "SELECT 1 FROM webhook_inbox WHERE body_digest = ? LIMIT 1",
                (body_digest,),
            ).fetchone() is not None:
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
