"""Opt-in PostgreSQL implementation of the workflow repository port."""

from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from agent_workflow.storage.postgres_connection import PostgresFactory
from agent_workflow.storage.repository import WorkflowError


class PostgresWorkflowUnit:
    def __init__(self, connection, *, writable: bool):
        self.connection = connection
        self.writable = writable

    def _execute(self, sql: str, values: tuple = ()):
        # Statements are fixed by this module; only values are bound parameters.
        parts = sql.split("?")
        if len(parts) != len(values) + 1:
            raise ValueError("incorrect number of SQL parameters")
        statement = parts[0] + "".join(
            f":v{i}{part}" for i, part in enumerate(parts[1:])
        )
        return self.connection.execute(
            text(statement), {f"v{i}": v for i, v in enumerate(values)}
        )

    def _one(self, sql: str, values: tuple = ()) -> dict | None:
        row = self._execute(sql, values).mappings().first()
        return dict(row) if row is not None else None

    def _all(self, sql: str, values: tuple = ()) -> list[dict]:
        return [dict(row) for row in self._execute(sql, values).mappings()]

    def insert_event(self, values: tuple = ()) -> None:
        self._execute(
            "INSERT INTO task_events(task_id,previous_status,next_status,action,sha,timestamp,metadata) VALUES(?,?,?,?,?,?,?)",
            values,
        )

    def set_status(self, values: tuple = ()) -> None:
        self._execute("UPDATE tasks SET status=?,updated_at=? WHERE task_id=?", values)

    def insert_task(self, values: tuple = ()) -> None:
        self._execute(
            "INSERT INTO tasks(task_id,metadata,repository,branch,base_sha,requested_effort,require_verified_effort,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            values,
        )

    def set_head(self, values: tuple = ()) -> None:
        self._execute("UPDATE tasks SET current_head_sha=? WHERE task_id=?", values)

    def set_rounds(self, values: tuple = ()) -> None:
        self._execute(
            "UPDATE tasks SET completed_review_rounds=? WHERE task_id=?", values
        )

    def insert_claim(self, values: tuple = ()) -> None:
        self._execute(
            "INSERT INTO review_attempts(token,task_id,expected_sha,reviewer,claimed_at,expires_at,status) VALUES(?,?,?,?,?,?,'ACTIVE')",
            values,
        )

    def expire_claim(self, values: tuple = ()) -> None:
        self._execute(
            "UPDATE review_attempts SET status='EXPIRED' WHERE token=?", values
        )

    def submit_claim(self, values: tuple = ()) -> None:
        self._execute(
            "UPDATE review_attempts SET status='SUBMITTED',submitted_payload=? WHERE token=?",
            values,
        )

    def insert_review(self, values: tuple = ()) -> None:
        self._execute("INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values)

    def insert_finding(self, values: tuple = ()) -> None:
        self._execute("INSERT INTO review_findings VALUES(?,?,?,?,?,?,?,?,?,?)", values)

    def find_task(self, values: tuple = ()) -> dict | None:
        lock = " FOR UPDATE" if self.writable else ""
        return self._one("SELECT * FROM tasks WHERE task_id=?" + lock, values)

    def find_authorization(self, values: tuple = ()) -> dict | None:
        # Unlike an existing row, a missing task cannot be locked with FOR UPDATE.
        # Serialize concurrent authorizations for the same task before checking it.
        if self.writable:
            self._execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(?, 5727811))", values
            )
        return self._one("SELECT metadata FROM tasks WHERE task_id=?", values)

    def find_active_claim(self, values: tuple = ()) -> dict | None:
        return self._one(
            "SELECT * FROM review_attempts WHERE task_id=? AND status='ACTIVE'", values
        )

    def find_claim(self, values: tuple = ()) -> dict | None:
        return self._one(
            "SELECT * FROM review_attempts WHERE token=? AND task_id=?", values
        )

    def list_claimed_tasks(self, values: tuple = ()) -> list[dict]:
        lock = " FOR UPDATE" if self.writable else ""
        return self._all(
            "SELECT * FROM tasks WHERE status='IN_REVIEW' ORDER BY task_id" + lock,
            values,
        )

    def list_ready_tasks(self, values: tuple = ()) -> list[dict]:
        return self._all(
            "SELECT * FROM tasks WHERE status='READY_IN_REVIEW' ORDER BY task_id",
            values,
        )

    def list_reviews(self, values: tuple = ()) -> list[dict]:
        return self._all(
            "SELECT * FROM reviews WHERE task_id=? ORDER BY round_number", values
        )

    def list_findings(self, values: tuple = ()) -> list[dict]:
        return self._all(
            "SELECT * FROM review_findings WHERE review_id=? ORDER BY finding_id",
            values,
        )

    def list_events(self, values: tuple = ()) -> list[dict]:
        return self._all(
            "SELECT * FROM task_events WHERE task_id=? ORDER BY event_id", values
        )

    def find_head_handoff(self, task_id: str, head_sha: str | None) -> dict | None:
        return self._one(
            "SELECT action,metadata FROM task_events WHERE task_id=? AND sha=? AND action IN ('HANDOFF','CI_REPAIR_HANDOFF') ORDER BY event_id DESC LIMIT 1",
            (task_id, head_sha),
        )

    def ci_approved(self, values: tuple = ()) -> bool:
        try:
            with self.connection.begin_nested():
                return (
                    self._one(
                        "SELECT 1 FROM controller_ci_tasks WHERE task_id=? AND head_sha=? AND verdict='SUCCESS' LIMIT 1",
                        values,
                    )
                    is not None
                )
        except DBAPIError:
            return False

    def advance_failed_ci_head(self, task_id: str, old_head: str, new_head: str) -> int:
        if new_head == old_head:
            raise WorkflowError("CI repair requires a different HEAD")
        try:
            with self.connection.begin_nested():
                row = self._one(
                    "SELECT c.pr_number FROM controller_ci_tasks c WHERE c.task_id=? AND c.head_sha=? AND c.verdict='FAILURE' AND c.pr_number>0 AND c.repair_heads<2 AND EXISTS (SELECT 1 FROM controller_ci_observations o WHERE o.task_id=c.task_id AND o.pr_number=c.pr_number AND o.head_sha=c.head_sha AND o.verdict=c.verdict AND o.reason='REQUIRED_CHECK_FAILED' AND o.check_run_id>0 AND o.workflow_run_id>0 AND o.run_attempt>0) AND NOT EXISTS (SELECT 1 FROM controller_ci_observations o WHERE o.task_id=c.task_id AND o.head_sha=?) FOR UPDATE",
                    (task_id, old_head, new_head),
                )
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) not in {"42P01", "42703"}:
                raise
            raise WorkflowError("CI repair evidence is unavailable") from exc
        if row is None:
            raise WorkflowError("CI repair HEAD not authorized")
        changed = self._execute(
            "UPDATE controller_ci_tasks SET head_sha=?,verdict='BLOCKED',repair_heads=repair_heads+1 WHERE task_id=?",
            (new_head, task_id),
        ).rowcount
        if changed != 1:
            raise WorkflowError("CI repair update failed")
        return row["pr_number"]

    def import_historical_head(self, values: tuple) -> None:
        raise WorkflowError(
            "historical import is unsupported by the PostgreSQL adapter"
        )

    def delete_synthetic_history(self, values: tuple) -> None:
        raise WorkflowError(
            "synthetic history deletion is unsupported by the PostgreSQL adapter"
        )


class PostgresWorkflowRepository:
    def __init__(self, factory: PostgresFactory):
        self.factory = factory

    @contextmanager
    def transaction(self):
        with self.factory.transaction() as connection:
            yield PostgresWorkflowUnit(connection, writable=True)

    @contextmanager
    def read(self):
        with self.factory.transaction() as connection:
            connection.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            )
            yield PostgresWorkflowUnit(connection, writable=False)
