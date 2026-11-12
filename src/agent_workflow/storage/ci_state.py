"""Durable, role-neutral CI gate state owned by the trusted Controller.

This records observations, not a webhook's self-asserted conclusion. Separate
migration component avoids modifying frozen workflow/webhook migrations.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import re
import sqlite3

from agent_workflow.domain.service import WorkflowService
from agent_workflow.domain.states import CiVerdict, WorkflowStatus
from agent_workflow.integrations.ci_gate import CiEvidence
from agent_workflow.integrations.github import REPOSITORY, WorkflowError, validate_sha
from agent_workflow.storage.migrations import (
    Migration, migrate, table_names, validate_schema,
)
from agent_workflow.storage.repository import SQLiteUnit
from agent_workflow.storage.webhook_inbox import WebhookInbox, WebhookNotification


def _create_ci(db: sqlite3.Connection) -> None:
    db.execute("""
        CREATE TABLE controller_ci_tasks (
            task_id TEXT PRIMARY KEY NOT NULL REFERENCES tasks(task_id),
            pr_number INTEGER NOT NULL CHECK(pr_number>0),
            head_sha TEXT NOT NULL,
            verdict TEXT NOT NULL CHECK(verdict IN ('BLOCKED','FAILURE','SUCCESS')),
            repair_heads INTEGER NOT NULL DEFAULT 0 CHECK(repair_heads BETWEEN 0 AND 2)
        )
    """)
    db.execute("""
        CREATE TABLE controller_ci_observations (
            id INTEGER PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES controller_ci_tasks(task_id),
            pr_number INTEGER NOT NULL,
            head_sha TEXT NOT NULL,
            verdict TEXT NOT NULL CHECK(verdict IN ('BLOCKED','FAILURE','SUCCESS')),
            reason TEXT NOT NULL,
            check_run_id INTEGER,
            observed_at TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE INDEX controller_ci_observation_lookup
        ON controller_ci_observations(task_id, head_sha, verdict)
    """)
    db.execute("""
        CREATE TABLE controller_ci_processed (
            delivery_id TEXT PRIMARY KEY NOT NULL REFERENCES webhook_inbox(delivery_id),
            processed_at REAL NOT NULL DEFAULT (unixepoch())
        )
    """)


def _add_ci_provenance(db: sqlite3.Connection) -> None:
    db.execute("ALTER TABLE controller_ci_observations ADD COLUMN workflow_run_id INTEGER")
    db.execute("ALTER TABLE controller_ci_observations ADD COLUMN run_attempt INTEGER")


_MIGRATIONS = (
    Migration(1, "controller_ci_gate", _create_ci),
    Migration(2, "controller_ci_provenance", _add_ci_provenance),
)
_OWNED = {"controller_ci_tasks", "controller_ci_observations", "controller_ci_processed"}


def _validate_ci(db: sqlite3.Connection, version: int) -> None:
    with closing(sqlite3.connect(":memory:")) as expected:
        # Foreign-key declarations can exist even when target tables aren't in
        # the in-memory validator; no data is written there.
        for step in _MIGRATIONS[:version]:
            step.upgrade(expected)
        validate_schema(db, expected, "controller_ci")


def _legacy_ci(db: sqlite3.Connection) -> int:
    if table_names(db) & _OWNED:
        raise sqlite3.DatabaseError("unregistered controller CI tables")
    return 0


class ControllerCiState:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        # These adapters separately validate the shared workflow/webhook DDL.
        WorkflowService(self.path)
        WebhookInbox(self.path)
        with closing(self._connect()) as db:
            migrate(db, "controller_ci", _MIGRATIONS,
                    adopt_legacy=_legacy_ci, validate=_validate_ci)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA synchronous=FULL")
        return db

    def track(self, task_id: str, pr_number: int, head_sha: str) -> None:
        """Register only after trusted worker handoff; at most 2 repair HEADs."""
        validate_sha(head_sha)
        if not isinstance(task_id, str) or not task_id or type(pr_number) is not int or pr_number < 1:
            raise WorkflowError("invalid CI tracking identity")
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            task = db.execute(
                "SELECT current_head_sha,status,repository FROM tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if (
                task is None or task["status"] != WorkflowStatus.READY_IN_REVIEW
                or task["current_head_sha"] != head_sha
                or task["repository"] != REPOSITORY
            ):
                raise WorkflowError("CI tracking requires authorized exact task HEAD")
            row = db.execute(
                "SELECT pr_number,head_sha "
                "FROM controller_ci_tasks WHERE task_id=?", (task_id,),
            ).fetchone()
            if row is None:
                inserted = db.execute(
                    "INSERT INTO controller_ci_tasks VALUES(?, ?, ?, ?, 0)",
                    (task_id, pr_number, head_sha, CiVerdict.BLOCKED),
                )
                if inserted.rowcount != 1:
                    raise WorkflowError("CI tracking insert failed")
                return
            if row["pr_number"] != pr_number:
                raise WorkflowError("CI tracking PR differs from persisted binding")
            if row["head_sha"] == head_sha:
                return
            # Retain tracking for legacy callers that already moved the task HEAD.
            SQLiteUnit(db).advance_failed_ci_head(task_id, row["head_sha"], head_sha)

    def tracked(self, limit: int = 32) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 32:
            raise ValueError("bounded CI task scan required")
        with closing(self._connect()) as db:
            records = db.execute(
                "SELECT c.task_id,c.pr_number,c.head_sha,c.verdict,c.repair_heads "
                "FROM controller_ci_tasks c JOIN tasks t ON t.task_id=c.task_id "
                "WHERE t.status='READY_IN_REVIEW' AND t.current_head_sha=c.head_sha "
                "ORDER BY c.task_id LIMIT ?", (limit + 1,),
            ).fetchall()
        if len(records) > limit:
            raise WorkflowError("too many in-flight CI tasks")
        return [dict(row) for row in records]

    def previous_failure(self, task_id: str, head_sha: str) -> bool:
        with closing(self._connect()) as db:
            return db.execute(
                "SELECT 1 FROM controller_ci_observations "
                "WHERE task_id=? AND head_sha=? AND verdict='FAILURE' LIMIT 1",
                (task_id, head_sha),
            ).fetchone() is not None

    def record(self, evidence: CiEvidence) -> None:
        if type(evidence) is not CiEvidence:
            raise WorkflowError("untrusted CI evidence type")
        if (
            evidence.check_name != "Python tests"
            or evidence.check_app_slug != "github-actions"
            or evidence.installation_id <= 0
        ):
            raise WorkflowError("CI evidence source is not authorized")
        terminal_reasons = {
            CiVerdict.SUCCESS: "REQUIRED_CHECK_PASSED",
            CiVerdict.FAILURE: "REQUIRED_CHECK_FAILED",
        }
        if evidence.verdict in terminal_reasons and (
            evidence.reason != terminal_reasons[evidence.verdict]
            or evidence.observed_head_sha != evidence.expected_head_sha
            or evidence.tested_sha != evidence.expected_head_sha
            or type(evidence.check_run_id) is not int
            or evidence.check_run_id <= 0
            or type(evidence.workflow_run_id) is not int
            or evidence.workflow_run_id <= 0
            or type(evidence.run_attempt) is not int
            or evidence.run_attempt <= 0
        ):
            raise WorkflowError(
                f"CI {evidence.verdict.lower()} lacks exact-HEAD trusted evidence"
            )
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT pr_number,head_sha FROM controller_ci_tasks WHERE task_id=?",
                (evidence.task_id,),
            ).fetchone()
            if (
                row is None or row["pr_number"] != evidence.pr_number
                or row["head_sha"] != evidence.expected_head_sha
                or evidence.repository != REPOSITORY
            ):
                raise WorkflowError("CI evidence mismatched task/PR/HEAD")
            inserted = db.execute(
                "INSERT INTO controller_ci_observations("
                "task_id,pr_number,head_sha,verdict,reason,check_run_id,observed_at,"
                "workflow_run_id,run_attempt)"
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (evidence.task_id, evidence.pr_number, evidence.expected_head_sha,
                 evidence.verdict, evidence.reason, evidence.check_run_id,
                 evidence.observed_at, evidence.workflow_run_id, evidence.run_attempt),
            )
            if inserted.rowcount != 1:
                raise WorkflowError("CI observation insertion failed")
            # A recorded failure on the SAME SHA cannot turn into SUCCESS via rerun.
            failure = db.execute(
                "SELECT 1 FROM controller_ci_observations WHERE task_id=? "
                "AND head_sha=? AND verdict='FAILURE' LIMIT 1",
                (evidence.task_id, evidence.expected_head_sha),
            ).fetchone()
            # Verified CI failure is sticky for this SHA: no later rerun,
            # missing API response or transient BLOCKED observation can open
            # review or revoke the task's eligibility for a repair HEAD.
            verdict = "FAILURE" if failure else evidence.verdict
            changed = db.execute(
                "UPDATE controller_ci_tasks SET verdict=? "
                "WHERE task_id=? AND head_sha=?",
                (verdict, evidence.task_id, evidence.expected_head_sha),
            ).rowcount
            if changed != 1:
                raise WorkflowError("CI verdict update failed")

    def approved(self, task_id: str, head_sha: str) -> bool:
        try:
            validate_sha(head_sha)
        except WorkflowError:
            return False
        with closing(self._connect()) as db:
            return db.execute(
                "SELECT 1 FROM controller_ci_tasks c JOIN tasks t "
                "ON c.task_id=t.task_id WHERE c.task_id=? "
                "AND c.head_sha=? AND c.verdict='SUCCESS' "
                "AND t.current_head_sha=c.head_sha LIMIT 1",
                (task_id, head_sha),
            ).fetchone() is not None

    def pending_events(self, limit: int = 20) -> list[WebhookNotification]:
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("bounded webhook scan required")
        with closing(self._connect()) as db:
            records = db.execute(
                "SELECT w.delivery_id,w.event,w.action,w.repository,"
                "w.installation_id,w.subject_id,w.head_sha "
                "FROM webhook_inbox w LEFT JOIN controller_ci_processed p "
                "ON p.delivery_id=w.delivery_id WHERE p.delivery_id IS NULL "
                "ORDER BY w.rowid LIMIT ?", (limit,),
            ).fetchall()
        return [WebhookNotification(*row) for row in records]

    def mark_processed(self, delivery_id: str) -> None:
        if not isinstance(delivery_id, str) or not re.fullmatch(
            r"[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}", delivery_id
        ):
            raise WorkflowError("invalid webhook delivery identity")
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM webhook_inbox WHERE delivery_id=?", (delivery_id,),
            ).fetchone() is None:
                raise WorkflowError("unknown webhook delivery")
            inserted = db.execute(
                "INSERT INTO controller_ci_processed(delivery_id) VALUES(?) "
                "ON CONFLICT(delivery_id) DO NOTHING",
                (delivery_id,),
            )
            if inserted.rowcount not in (0, 1):
                raise WorkflowError("CI event settlement failed")
