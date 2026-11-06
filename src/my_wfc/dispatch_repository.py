"""SQLite dispatch storage adapter; no Git/GitHub capabilities."""

import time
import uuid
from typing import Callable
from my_wfc.github import WorkflowError, validate_sha
from my_wfc.service import WorkflowService

REPOSITORY = "builacviet/techlead-review-poc"


class DispatchLedger:
    def __init__(
        self, service: WorkflowService, *, clock: Callable = time.time
    ) -> None:
        self.service, self.clock = service, clock
        with service.repository.raw_connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS coder_dispatches (
                    dispatch_id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
                    round_number INTEGER NOT NULL, input_sha TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('ACTIVE','BLOCKED','DONE','ACKNOWLEDGED')),
                    slot INTEGER UNIQUE CHECK(slot=1), deadline REAL NOT NULL,
                    result_sha TEXT, detail TEXT NOT NULL DEFAULT '',
                    UNIQUE(task_id,round_number,input_sha)
                );
            """)

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
                    or task["repository"] != REPOSITORY
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
