"""PostgreSQL trusted CI ledger (no migration DDL, no webhook CI decisions).

The authoritative CI result still comes only from the verified read-only
GitHub App and the exact-HEAD checks/Actions provenance adapter.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from agent_workflow.integrations.ci_gate import CiEvidence
from agent_workflow.integrations.github import REPOSITORY, WorkflowError, validate_sha
from agent_workflow.storage.webhook_inbox import WebhookNotification

_NAMESPACE = 0x41474657
_TRANSACTION_KEY = 10


class PostgresControllerCiState:
    def __init__(self, dsn: str):
        if not isinstance(dsn, str) or not dsn.startswith(("postgresql://", "postgres://")):
            raise ValueError("PostgreSQL DSN required")
        self.dsn = dsn

    @contextmanager
    def _connect(self) -> Iterator:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(
            self.dsn, autocommit=True, row_factory=dict_row, connect_timeout=8,
        ) as db:
            yield db

    @contextmanager
    def _transaction(self) -> Iterator:
        with self._connect() as db:
            with db.transaction():
                db.execute(
                    "SELECT pg_advisory_xact_lock(%s, %s)",
                    (_NAMESPACE, _TRANSACTION_KEY),
                )
                yield db

    def track(self, task_id: str, pr_number: int, head_sha: str) -> None:
        validate_sha(head_sha)
        if not isinstance(task_id, str) or not task_id or type(pr_number) is not int or pr_number < 1:
            raise WorkflowError("invalid CI tracking identity")
        with self._transaction() as db:
            task = db.execute(
                "SELECT current_head_sha,status,repository FROM workflow.tasks WHERE task_id=%s",
                (task_id,),
            ).fetchone()
            if (
                task is None or task["status"] != "READY_IN_REVIEW"
                or task["current_head_sha"] != head_sha
                or task["repository"] != REPOSITORY
            ):
                raise WorkflowError("CI tracking requires authorized exact task HEAD")
            row = db.execute(
                "SELECT pr_number,head_sha,verdict,repair_heads "
                "FROM workflow.controller_ci_tasks WHERE task_id=%s", (task_id,),
            ).fetchone()
            if row is None:
                inserted = db.execute(
                    "INSERT INTO workflow.controller_ci_tasks "
                    "(task_id,pr_number,head_sha,verdict,repair_heads) "
                    "VALUES(%s,%s,%s,'BLOCKED',0)",
                    (task_id, pr_number, head_sha),
                )
                if inserted.rowcount != 1:
                    raise WorkflowError("CI tracking insert failed")
                return
            if row["head_sha"] == head_sha and row["pr_number"] == pr_number:
                return
            if (
                row["pr_number"] != pr_number
                or row["head_sha"] == head_sha
                or row["verdict"] != "FAILURE"
                or row["repair_heads"] >= 2
            ):
                raise WorkflowError("CI repair HEAD not authorized")
            changed = db.execute(
                "UPDATE workflow.controller_ci_tasks "
                "SET head_sha=%s,verdict='BLOCKED',repair_heads=repair_heads+1 "
                "WHERE task_id=%s",
                (head_sha, task_id),
            ).rowcount
            if changed != 1:
                raise WorkflowError("CI tracking update failed")

    def tracked(self, limit: int = 32) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 32:
            raise ValueError("bounded CI task scan required")
        with self._connect() as db:
            rows = db.execute(
                "SELECT c.task_id,c.pr_number,c.head_sha,c.verdict,c.repair_heads "
                "FROM workflow.controller_ci_tasks c "
                "JOIN workflow.tasks t ON t.task_id=c.task_id "
                "WHERE t.status='READY_IN_REVIEW' AND t.current_head_sha=c.head_sha "
                "ORDER BY c.task_id LIMIT %s",
                (limit + 1,),
            ).fetchall()
        if len(rows) > limit:
            raise WorkflowError("too many in-flight CI tasks")
        return [dict(row) for row in rows]

    def previous_failure(self, task_id: str, head_sha: str) -> bool:
        with self._connect() as db:
            return db.execute(
                "SELECT 1 FROM workflow.controller_ci_observations "
                "WHERE task_id=%s AND head_sha=%s AND verdict='FAILURE' LIMIT 1",
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
        if evidence.verdict == "SUCCESS" and (
            evidence.reason != "REQUIRED_CHECK_PASSED"
            or evidence.observed_head_sha != evidence.expected_head_sha
            or evidence.tested_sha != evidence.expected_head_sha
            or type(evidence.check_run_id) is not int
            or evidence.check_run_id <= 0
            or type(evidence.workflow_run_id) is not int
            or evidence.workflow_run_id <= 0
            or type(evidence.run_attempt) is not int
            or evidence.run_attempt <= 0
        ):
            raise WorkflowError("CI success lacks exact-HEAD trusted evidence")

        with self._transaction() as db:
            row = db.execute(
                "SELECT pr_number,head_sha FROM workflow.controller_ci_tasks WHERE task_id=%s",
                (evidence.task_id,),
            ).fetchone()
            if (
                row is None or row["pr_number"] != evidence.pr_number
                or row["head_sha"] != evidence.expected_head_sha
                or evidence.repository != REPOSITORY
            ):
                raise WorkflowError("CI evidence mismatched task/PR/HEAD")
            inserted = db.execute(
                "INSERT INTO workflow.controller_ci_observations "
                "(task_id,pr_number,head_sha,verdict,reason,check_run_id,observed_at,workflow_run_id,run_attempt) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (evidence.task_id, evidence.pr_number, evidence.expected_head_sha,
                 evidence.verdict, evidence.reason, evidence.check_run_id,
                 evidence.observed_at, evidence.workflow_run_id, evidence.run_attempt),
            )
            if inserted.rowcount != 1:
                raise WorkflowError("CI observation insertion failed")
            failure = db.execute(
                "SELECT 1 FROM workflow.controller_ci_observations "
                "WHERE task_id=%s AND head_sha=%s AND verdict='FAILURE' LIMIT 1",
                (evidence.task_id, evidence.expected_head_sha),
            ).fetchone()
            verdict = "FAILURE" if failure else evidence.verdict
            changed = db.execute(
                "UPDATE workflow.controller_ci_tasks SET verdict=%s "
                "WHERE task_id=%s AND head_sha=%s",
                (verdict, evidence.task_id, evidence.expected_head_sha),
            ).rowcount
            if changed != 1:
                raise WorkflowError("CI verdict update failed")

    def approved(self, task_id: str, head_sha: str) -> bool:
        try:
            validate_sha(head_sha)
        except WorkflowError:
            return False
        with self._connect() as db:
            return db.execute(
                "SELECT 1 FROM workflow.controller_ci_tasks c "
                "JOIN workflow.tasks t ON t.task_id=c.task_id "
                "WHERE c.task_id=%s AND c.head_sha=%s AND c.verdict='SUCCESS' "
                "AND t.current_head_sha=c.head_sha LIMIT 1",
                (task_id, head_sha),
            ).fetchone() is not None

    def pending_events(self, limit: int = 20) -> list[WebhookNotification]:
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("bounded webhook scan required")
        with self._connect() as db:
            rows = db.execute(
                "SELECT w.delivery_id,w.event,w.action,w.repository,w.installation_id,"
                "w.subject_id,w.head_sha FROM workflow.webhook_inbox w "
                "LEFT JOIN workflow.controller_ci_processed p ON p.delivery_id=w.delivery_id "
                "WHERE p.delivery_id IS NULL ORDER BY w.delivery_id LIMIT %s",
                (limit,),
            ).fetchall()
        return [WebhookNotification(
            row["delivery_id"], row["event"], row["action"], row["repository"],
            row["installation_id"], row["subject_id"], row["head_sha"],
        ) for row in rows]

    def mark_processed(self, delivery_id: str) -> None:
        import re
        if not isinstance(delivery_id, str) or not re.fullmatch(
            r"[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}", delivery_id,
        ):
            raise WorkflowError("invalid webhook delivery identity")
        with self._transaction() as db:
            if db.execute(
                "SELECT 1 FROM workflow.webhook_inbox WHERE delivery_id=%s",
                (delivery_id,),
            ).fetchone() is None:
                raise WorkflowError("unknown webhook delivery")
            changed = db.execute(
                "INSERT INTO workflow.controller_ci_processed(delivery_id) VALUES(%s) "
                "ON CONFLICT(delivery_id) DO NOTHING", (delivery_id,),
            ).rowcount
            if changed not in (0, 1):
                raise WorkflowError("CI event settlement failed")
