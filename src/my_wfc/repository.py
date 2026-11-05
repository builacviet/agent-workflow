"""Storage interface and SQLite adapter. SQL exists only in this adapter."""

from contextlib import contextmanager, AbstractContextManager
from pathlib import Path
import sqlite3
from typing import Protocol
from my_wfc.budget import check_deadline, operation_timeout
from my_wfc.github import WorkflowError


class WorkflowUnit(Protocol):
    def import_historical_head(self, values: tuple) -> None: ...
    def delete_synthetic_history(self, values: tuple) -> None: ...
    def insert_event(self, values: tuple = ()) -> None: ...
    def set_status(self, values: tuple = ()) -> None: ...
    def list_reviews(self, values: tuple = ()) -> list[dict]: ...
    def insert_task(self, values: tuple = ()) -> None: ...
    def set_head(self, values: tuple = ()) -> None: ...
    def expire_claim(self, values: tuple = ()) -> None: ...
    def insert_claim(self, values: tuple = ()) -> None: ...
    def insert_review(self, values: tuple = ()) -> None: ...
    def set_rounds(self, values: tuple = ()) -> None: ...
    def submit_claim(self, values: tuple = ()) -> None: ...
    def insert_finding(self, values: tuple = ()) -> None: ...
    def find_task(self, values: tuple = ()) -> dict | None: ...
    def find_active_claim(self, values: tuple = ()) -> dict | None: ...
    def find_authorization(self, values: tuple = ()) -> dict | None: ...
    def list_claimed_tasks(self, values: tuple = ()) -> list[dict]: ...
    def list_ready_tasks(self, values: tuple = ()) -> list[dict]: ...
    def list_findings(self, values: tuple = ()) -> list[dict]: ...
    def list_events(self, values: tuple = ()) -> list[dict]: ...
    def find_claim(self, values: tuple = ()) -> dict | None: ...


class WorkflowRepository(Protocol):
    def transaction(self) -> AbstractContextManager[WorkflowUnit]: ...
    def read(self) -> AbstractContextManager[WorkflowUnit]: ...


class SQLiteUnit:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def insert_event(self, values: tuple = ()) -> None:
        self.db.execute(
            "INSERT INTO task_events(task_id,previous_status,next_status,action,sha,timestamp,metadata) VALUES(?,?,?,?,?,?,?)",
            values,
        )

    def set_status(self, values: tuple = ()) -> None:
        self.db.execute(
            "UPDATE tasks SET status=?,updated_at=? WHERE task_id=?", values
        )

    def list_reviews(self, values: tuple = ()) -> list[dict]:
        cursor = self.db.execute(
            "SELECT * FROM reviews WHERE task_id=? ORDER BY round_number",
            values,
        )
        return [dict(row) for row in cursor.fetchall()]

    def insert_task(self, values: tuple = ()) -> None:
        self.db.execute(
            "INSERT INTO tasks(task_id,metadata,repository,branch,base_sha,requested_effort,require_verified_effort,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            values,
        )

    def set_head(self, values: tuple = ()) -> None:
        self.db.execute(
            "UPDATE tasks SET current_head_sha=? WHERE task_id=?", values
        )

    def expire_claim(self, values: tuple = ()) -> None:
        self.db.execute(
            "UPDATE review_attempts SET status='EXPIRED' WHERE token=?", values
        )

    def insert_claim(self, values: tuple = ()) -> None:
        self.db.execute(
            "INSERT INTO review_attempts(token,task_id,expected_sha,reviewer,claimed_at,expires_at,status) VALUES(?,?,?,?,?,?,'ACTIVE')",
            values,
        )

    def insert_review(self, values: tuple = ()) -> None:
        self.db.execute(
            "INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values
        )

    def set_rounds(self, values: tuple = ()) -> None:
        self.db.execute(
            "UPDATE tasks SET completed_review_rounds=? WHERE task_id=?",
            values,
        )

    def submit_claim(self, values: tuple = ()) -> None:
        self.db.execute(
            "UPDATE review_attempts SET status='SUBMITTED',submitted_payload=? WHERE token=?",
            values,
        )

    def insert_finding(self, values: tuple = ()) -> None:
        self.db.execute(
            "INSERT INTO review_findings VALUES(?,?,?,?,?,?,?,?,?,?)", values
        )

    def find_task(self, values: tuple = ()) -> dict | None:
        cursor = self.db.execute("SELECT * FROM tasks WHERE task_id=?", values)
        row = cursor.fetchone()
        return dict(row) if row is not None else None

    def find_active_claim(self, values: tuple = ()) -> dict | None:
        cursor = self.db.execute(
            "SELECT * FROM review_attempts WHERE task_id=? AND status='ACTIVE'",
            values,
        )
        row = cursor.fetchone()
        return dict(row) if row is not None else None

    def find_authorization(self, values: tuple = ()) -> dict | None:
        cursor = self.db.execute(
            "SELECT metadata FROM tasks WHERE task_id=?", values
        )
        row = cursor.fetchone()
        return dict(row) if row is not None else None

    def list_claimed_tasks(self, values: tuple = ()) -> list[dict]:
        cursor = self.db.execute(
            "SELECT * FROM tasks WHERE status='IN_REVIEW'", values
        )
        return [dict(row) for row in cursor.fetchall()]

    def list_ready_tasks(self, values: tuple = ()) -> list[dict]:
        cursor = self.db.execute(
            "SELECT * FROM tasks WHERE status='READY_IN_REVIEW' ORDER BY task_id",
            values,
        )
        return [dict(row) for row in cursor.fetchall()]

    def list_findings(self, values: tuple = ()) -> list[dict]:
        cursor = self.db.execute(
            "SELECT * FROM review_findings WHERE review_id=? ORDER BY finding_id",
            values,
        )
        return [dict(row) for row in cursor.fetchall()]

    def list_events(self, values: tuple = ()) -> list[dict]:
        cursor = self.db.execute(
            "SELECT * FROM task_events WHERE task_id=? ORDER BY event_id",
            values,
        )
        return [dict(row) for row in cursor.fetchall()]

    def find_claim(self, values: tuple = ()) -> dict | None:
        cursor = self.db.execute(
            "SELECT * FROM review_attempts WHERE token=? AND task_id=?", values
        )
        row = cursor.fetchone()
        return dict(row) if row is not None else None

    def import_historical_head(self, values: tuple) -> None:
        self.db.execute(
            "UPDATE tasks SET current_head_sha=?,completed_review_rounds=? WHERE task_id='TASK-001'",
            values,
        )

    def delete_synthetic_history(self, values: tuple) -> None:
        table, task_id = values
        if (
            table
            not in {
                "task_events",
                "review_findings",
                "reviews",
                "review_attempts",
                "tasks",
            }
            or task_id != "POC-REVIEW-001"
        ):
            raise WorkflowError(
                "only disposable synthetic history can be deleted"
            )
        self.db.execute(f"DELETE FROM {table} WHERE task_id=?", (task_id,))


class SQLiteWorkflowRepository:
    def __init__(self, db_path):
        if db_path is None:
            raise ValueError("database path or repository required")
        self.db_path = str(db_path)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self.raw_connection() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] not in (0, 1):
                raise WorkflowError("unsupported database schema version")
            db.executescript(
                Path(__file__).with_name("schema.sql").read_text()
            )

    @contextmanager
    def raw_connection(self):
        db = sqlite3.connect(
            self.db_path, timeout=operation_timeout(30), isolation_level=None
        )
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def raw_transaction(self):
        with self.raw_connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                check_deadline()
                db.execute(
                    "PRAGMA busy_timeout=%d"
                    % int(operation_timeout(30) * 1000)
                )
                db.commit()
            except Exception:
                db.rollback()
                raise

    @contextmanager
    def transaction(self):
        with self.raw_transaction() as db:
            yield SQLiteUnit(db)

    @contextmanager
    def read(self):
        with self.raw_connection() as db:
            db.execute("BEGIN")
            yield SQLiteUnit(db)
