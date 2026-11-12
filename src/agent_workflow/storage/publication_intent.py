"""Durable publication intent journal for trusted Controller reconciliation.

No GitHub or Docker side effects occur here. A prepared intent MUST be committed
before external publish effects. A crash preserves the dispatch slot; a remote
receipt never by itself releases that slot or authorizes review/merge.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from pathlib import Path
import re
import sqlite3
import time
from typing import Iterator

from agent_workflow.storage.repository import SQLiteWorkflowRepository
from agent_workflow.storage.migrations import (
    Migration, migrate, table_names, validate_schema,
)


def _create_intents(db: sqlite3.Connection) -> None:
    db.execute("""
        CREATE TABLE publication_intents (
            dispatch_id TEXT PRIMARY KEY NOT NULL
                REFERENCES coder_dispatches(dispatch_id),
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            repository TEXT NOT NULL,
            branch TEXT NOT NULL,
            base_sha TEXT NOT NULL,
            input_sha TEXT NOT NULL,
            expected_head_sha TEXT NOT NULL,
            tested_digest TEXT NOT NULL,
            state TEXT NOT NULL
                CHECK(state IN ('PREPARED','UNCERTAIN','VERIFIED')),
            pr_number INTEGER CHECK(pr_number>0),
            pr_url TEXT,
            created_at REAL NOT NULL,
            verified_at REAL,
            CHECK(
                (state='VERIFIED' AND pr_number IS NOT NULL
                    AND pr_url IS NOT NULL AND verified_at IS NOT NULL)
                OR (state!='VERIFIED' AND pr_number IS NULL
                    AND pr_url IS NULL AND verified_at IS NULL)
            )
        )
    """)


_MIGRATIONS = (Migration(1, "publication_intent", _create_intents),)


def _validate(db: sqlite3.Connection, version: int) -> None:
    with closing(sqlite3.connect(":memory:")) as expected:
        for step in _MIGRATIONS[:version]:
            step.upgrade(expected)
        validate_schema(db, expected, "publication_intents")


def _legacy(db: sqlite3.Connection) -> int:
    if "publication_intents" in table_names(db):
        raise sqlite3.DatabaseError("unregistered publication intent table")
    return 0


class PublicationError(ValueError):
    """Fail-closed local publication state or authorization error."""


def _validate_sha(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise PublicationError("invalid publication commit SHA")


class PublicationIntentStore:
    """SQLite foundation for R1-04B; later PG adapter needs atomic fencing.

    This is deliberately not wired into the worker's publish path until an
    offline *expected* commit SHA can be persisted before any remote write.
    """

    def __init__(self, path: str | Path, *, clock=time.time) -> None:
        self.path = Path(path).resolve()
        self.clock = clock
        SQLiteWorkflowRepository(self.path)  # Ensure tasks/dispatch schema exists.
        with closing(self._connect()) as db:
            migrate(
                db, "publication_intent", _MIGRATIONS,
                adopt_legacy=_legacy, validate=_validate,
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA synchronous=FULL")
        return db

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def _dispatch_id(value: str) -> None:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
            raise PublicationError("invalid publication dispatch identifier")

    def prepare(
        self, dispatch_id: str, task_id: str, branch: str,
        base_sha: str, input_sha: str, expected_head_sha: str,
        tested_digest: str,
    ) -> None:
        """Persist unique immutable intent BEFORE remote Git push or PR writes.

        The caller must produce and validate the commit SHA offline first.
        Reissuing even an identical intent is deliberately forbidden: never
        confuse a replay with authorization to repeat a remote write.
        """
        self._dispatch_id(dispatch_id)
        _validate_sha(base_sha)
        _validate_sha(input_sha)
        _validate_sha(expected_head_sha)
        if (
            expected_head_sha == input_sha
            or not isinstance(tested_digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", tested_digest)
        ):
            raise PublicationError("invalid publication evidence")
        with self._transaction() as db:
            row = db.execute(
                "SELECT d.task_id,d.input_sha,d.status,d.slot,"
                "t.repository,t.branch,t.base_sha "
                "FROM coder_dispatches d JOIN tasks t ON t.task_id=d.task_id "
                "WHERE d.dispatch_id=?", (dispatch_id,),
            ).fetchone()
            if (
                row is None or row["status"] != "ACTIVE" or row["slot"] != 1
                or row["task_id"] != task_id or row["input_sha"] != input_sha
                or row["branch"] != branch or row["base_sha"] != base_sha
            ):
                raise PublicationError("publication intent differs from active authorization")
            if db.execute(
                "SELECT 1 FROM publication_intents WHERE dispatch_id=?",
                (dispatch_id,),
            ).fetchone():
                raise PublicationError("publication intent already exists; never retry automatically")
            db.execute(
                "INSERT INTO publication_intents "
                "(dispatch_id,task_id,repository,branch,base_sha,input_sha,"
                "expected_head_sha,tested_digest,state,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,'PREPARED',?)",
                (dispatch_id, task_id, row["repository"], branch, base_sha, input_sha,
                 expected_head_sha, tested_digest, self.clock()),
            )

    def mark_uncertain(self, dispatch_id: str) -> None:
        """Atomically record ambiguity and retain the unique dispatch slot."""
        self._dispatch_id(dispatch_id)
        with self._transaction() as db:
            record = db.execute(
                "SELECT state FROM publication_intents WHERE dispatch_id=?",
                (dispatch_id,),
            ).fetchone()
            if record is None or record["state"] not in {"PREPARED", "UNCERTAIN"}:
                raise PublicationError("publication intent unavailable for uncertainty")
            updated = db.execute(
                "UPDATE coder_dispatches SET status='BLOCKED' "
                "WHERE dispatch_id=? AND slot=1 AND status IN ('ACTIVE','BLOCKED')",
                (dispatch_id,),
            ).rowcount
            if updated != 1:
                raise PublicationError("publication ambiguity lost its dispatch slot")
            db.execute(
                "UPDATE publication_intents SET state='UNCERTAIN' "
                "WHERE dispatch_id=? AND state='PREPARED'", (dispatch_id,),
            )

    def get(self, dispatch_id: str) -> dict:
        self._dispatch_id(dispatch_id)
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT * FROM publication_intents WHERE dispatch_id=?",
                (dispatch_id,),
            ).fetchone()
        if row is None:
            raise PublicationError("unknown publication intent")
        return dict(row)

    def record_verified(
        self, dispatch_id: str, head_sha: str, pr_number: int, pr_url: str,
    ) -> None:
        """Persist *independently verified* remote receipt, NEVER unblock slot."""
        self._dispatch_id(dispatch_id)
        _validate_sha(head_sha)
        if type(pr_number) is not int or pr_number <= 0 or not isinstance(pr_url, str):
            raise PublicationError("invalid publication receipt")
        with self._transaction() as db:
            row = db.execute(
                "SELECT i.state,i.expected_head_sha,i.repository,d.status,d.slot "
                "FROM publication_intents i JOIN coder_dispatches d "
                "ON d.dispatch_id=i.dispatch_id WHERE i.dispatch_id=?",
                (dispatch_id,),
            ).fetchone()
            if (
                row is None or row["state"] != "UNCERTAIN"
                or row["expected_head_sha"] != head_sha
                or row["status"] != "BLOCKED" or row["slot"] != 1
            ):
                raise PublicationError("publication receipt lacks blocked matching intent")
            if pr_url != f"https://github.com/{row['repository']}/pull/{pr_number}":
                raise PublicationError("publication PR reference differs from intent")
            changed = db.execute(
                "UPDATE publication_intents SET state='VERIFIED', "
                "pr_number=?,pr_url=?,verified_at=? "
                "WHERE dispatch_id=? AND state='UNCERTAIN'",
                (pr_number, pr_url, self.clock(), dispatch_id),
            ).rowcount
            if changed != 1:
                raise PublicationError("publication receipt update failed")
