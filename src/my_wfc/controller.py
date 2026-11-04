"""Durable single-slot dispatch. Uncertain executions require reconciliation."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
import uuid
from pathlib import Path
from contextlib import contextmanager
import os
from typing import Callable

from my_wfc.app_auth import AppVerifier, GitHubApp, REPOSITORY
from my_wfc.budget import dispatch_budget
from my_wfc.github import WorkflowError, validate_sha
from my_wfc.service import WorkflowService


class DispatchLedger:
    def __init__(
        self, service: WorkflowService, *, clock: Callable = time.time
    ) -> None:
        self.service, self.clock = service, clock
        with service.connection() as db:
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
        with self.service.transaction() as db:
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
        with self.service.transaction() as db:
            changed = db.execute(
                "UPDATE coder_dispatches SET status='DONE',slot=NULL,result_sha=? WHERE dispatch_id=? AND status='ACTIVE' AND slot=1",
                (sha, dispatch_id),
            ).rowcount
            if changed != 1:
                raise WorkflowError("dispatch lease lost")

    def block(self, dispatch_id: str) -> None:
        with self.service.transaction() as db:
            db.execute(
                "UPDATE coder_dispatches SET status='BLOCKED',detail='Operator reconciliation required; do not retry automatically' WHERE dispatch_id=? AND slot=1",
                (dispatch_id,),
            )

    def acknowledge(self, dispatch_id: str) -> None:
        """Operator must first stop/inspect containers and reconcile external effects."""
        with self.service.transaction() as db:
            changed = db.execute(
                "UPDATE coder_dispatches SET status='ACKNOWLEDGED',slot=NULL WHERE dispatch_id=? AND slot=1",
                (dispatch_id,),
            ).rowcount
            if changed != 1:
                raise WorkflowError("unknown active dispatch")


def run_once(
    service: WorkflowService,
    ledger: DispatchLedger,
    task_ids: set[str],
    execute: Callable[[dict, dict], str],
    timeout: int = 1800,
) -> bool:
    claim = ledger.claim(task_ids, timeout)
    if claim is None:
        return False
    try:
        with dispatch_budget(claim["deadline"]):
            context = service.get_coder_assignment(claim["task_id"])
            if (
                context["completed_review_rounds"] != claim["round_number"]
                or context["status"]
                not in {"AWAITING_HANDOFF", "CHANGES_REQUESTED"}
                or (context["current_head_sha"] or context["base_sha"])
                != claim["input_sha"]
            ):
                raise WorkflowError("assignment changed after dispatch")
            sha = execute(claim, context)
            ledger.finish(claim["dispatch_id"], sha)
    except BaseException:
        ledger.block(claim["dispatch_id"])
        raise
    return True


@contextmanager
def process_lock(path: Path):
    """Exclude operator reconciliation while a controller process is alive."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0)
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise WorkflowError(
                "stop the existing controller before reconciliation"
            ) from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--acknowledge", metavar="DISPATCH_ID")
    parser.add_argument("--reconciled", action="store_true")
    args = parser.parse_args()
    config = json.loads(
        args.config.resolve(strict=True).read_text(encoding="utf-8-sig")
    )
    from my_wfc.coder_runtime import validate_config

    validate_config(config)
    with process_lock(
        Path(config["database"]).with_suffix(".controller.lock")
    ):
        operate(config, args)


def operate(config: dict, args: argparse.Namespace) -> None:
    from my_wfc.coder_runtime import CoderRuntime

    app = GitHubApp(
        config["app_id"], config["installation_id"], Path(config["pem"])
    )
    service = WorkflowService(config["database"], AppVerifier(app))
    ledger = DispatchLedger(service)
    runtime = CoderRuntime(config, app, service)
    if args.acknowledge:
        if not args.reconciled:
            raise WorkflowError(
                "operator must reconcile Git/PR/handoff first; use --reconciled"
            )
        runtime.stop(args.acknowledge)
        ledger.acknowledge(args.acknowledge)
        return
    try:
        while True:
            try:
                run_once(
                    service,
                    ledger,
                    set(config["tasks"]),
                    runtime.execute,
                    config.get("timeout_seconds", 1800),
                )
            except (WorkflowError, OSError, sqlite3.Error, ValueError):
                # Never log subprocess output, prompts, credentials or task content.
                print(
                    "Controller blocked or unavailable; inspect local dispatch ledger",
                    flush=True,
                )
                if args.once:
                    raise WorkflowError(
                        "Controller attempt failed; operator reconciliation may be required"
                    ) from None
            if args.once:
                return
            time.sleep(60)
    finally:
        app.revoke()


if __name__ == "__main__":
    main()
