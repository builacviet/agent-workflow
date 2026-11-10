"""SQLite dispatch storage adapter; no Git/GitHub capabilities."""

import time
import uuid
import re
from typing import Callable

from agent_workflow.integrations.github import (
    REPOSITORY,
    WorkflowError,
    canonical_repository,
    validate_sha,
)
from agent_workflow.domain.service import WorkflowService


class DispatchLedger:
    def __init__(
        self, service: WorkflowService, *, clock: Callable = time.time
    ) -> None:
        self.service, self.clock = service, clock

    def record_runtime_evidence(
        self, dispatch_id: str, task_id: str, input_sha: str,
        runtime_name: str, declared_version: str,
        image_id: str | None, telemetry: str,
        tested_digest: str, published_head_sha: str,
    ) -> None:
        """Store validated host evidence before reporting a task handoff."""
        validate_sha(input_sha)
        validate_sha(published_head_sha)
        if (
            not re.fullmatch(r"[0-9a-f]{32}", dispatch_id)
            or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", runtime_name)
            or not isinstance(declared_version, str) or not declared_version.strip()
            or (image_id is not None and not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id))
            or telemetry != "UNVERIFIED"
            or not re.fullmatch(r"[a-f0-9]{64}", tested_digest)
        ):
            raise WorkflowError("invalid runtime evidence")
        with self.service.repository.raw_transaction() as db:
            row = db.execute(
                "SELECT task_id,input_sha,status FROM coder_dispatches "
                "WHERE dispatch_id=? AND slot=1", (dispatch_id,)
            ).fetchone()
            if row is None or row["task_id"] != task_id or row["input_sha"] != input_sha or row["status"] != "ACTIVE":
                raise WorkflowError("runtime evidence has no active matching dispatch")
            inserted = db.execute(
                "INSERT INTO coder_runtime_evidence VALUES(?,?,?,?,?,?,?,?,?,?)",
                (dispatch_id, task_id, input_sha, runtime_name, declared_version,
                 image_id, telemetry, tested_digest, published_head_sha, self.clock()),
            )
            if inserted.rowcount != 1:
                raise WorkflowError("runtime evidence was not inserted")

    def claim(self, task_ids: set[str], timeout: int) -> dict | None:
        if type(timeout) is not int or not 60 <= timeout <= 3600:
            raise WorkflowError("runtime timeout must be 60..3600 seconds")
        with self.service.repository.raw_transaction() as db:
            if db.execute(
                "SELECT 1 FROM coder_dispatches WHERE slot=1"
            ).fetchone():
                return None
            for row in db.execute(
                "SELECT * FROM tasks WHERE status IN ('AWAITING_HANDOFF','CHANGES_REQUESTED') AND completed_review_rounds<2 ORDER BY task_id"
            ):
                task = dict(row)
                if (
                    task["task_id"] not in task_ids
                    or canonical_repository(task["repository"]) != REPOSITORY
                ):
                    continue
                sha = task["current_head_sha"] or task["base_sha"]
                validate_sha(sha)
                round_number = task["completed_review_rounds"]
                if db.execute(
                    "SELECT 1 FROM coder_dispatches WHERE task_id=? AND round_number=? AND input_sha=?",
                    (task["task_id"], round_number, sha),
                ).fetchone():
                    continue
                dispatch_id = uuid.uuid4().hex
                deadline = self.clock() + timeout
                db.execute(
                    "INSERT INTO coder_dispatches(dispatch_id,task_id,round_number,input_sha,status,slot,deadline) VALUES(?,?,?,?,'ACTIVE',1,?)",
                    (
                        dispatch_id,
                        task["task_id"],
                        round_number,
                        sha,
                        deadline,
                    ),
                )
                return {
                    "dispatch_id": dispatch_id,
                    "task_id": task["task_id"],
                    "input_sha": sha,
                    "deadline": deadline,
                    "round_number": round_number,
                }
        return None

    def finish(self, dispatch_id: str, sha: str) -> None:
        validate_sha(sha)
        with self.service.repository.raw_transaction() as db:
            changed = db.execute(
                "UPDATE coder_dispatches SET status='DONE',slot=NULL,result_sha=? WHERE dispatch_id=? AND status='ACTIVE' AND slot=1",
                (sha, dispatch_id),
            ).rowcount
            if changed != 1:
                raise WorkflowError("dispatch lease lost")

    def block(self, dispatch_id: str) -> None:
        with self.service.repository.raw_transaction() as db:
            db.execute(
                "UPDATE coder_dispatches SET status='BLOCKED',detail='Operator reconciliation required; do not retry automatically' WHERE dispatch_id=? AND slot=1",
                (dispatch_id,),
            )

    def acknowledge(self, dispatch_id: str) -> None:
        """Operator must first stop/inspect containers and reconcile external effects."""
        with self.service.repository.raw_transaction() as db:
            changed = db.execute(
                "UPDATE coder_dispatches SET status='ACKNOWLEDGED',slot=NULL WHERE dispatch_id=? AND slot=1",
                (dispatch_id,),
            ).rowcount
            if changed != 1:
                raise WorkflowError("unknown active dispatch")
