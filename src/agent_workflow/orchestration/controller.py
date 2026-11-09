"""Durable single-slot dispatch. Uncertain executions require reconciliation."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from contextlib import contextmanager
import os
from typing import Callable

from agent_workflow.budget import dispatch_budget
from agent_workflow.domain.service import WorkflowService
from agent_workflow.integrations.github import WorkflowError
from agent_workflow.storage.dispatch_repository import DispatchLedger


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
    from agent_workflow.runners.coder_runtime import validate_config

    validate_config(config)
    with process_lock(
        Path(config["database"]).with_suffix(".controller.lock")
    ):
        operate(config, args)


def operate(config: dict, args: argparse.Namespace) -> None:
    from agent_workflow.runners.coder_runtime import CoderRuntime

    service = WorkflowService(config["database"])
    ledger = DispatchLedger(service)
    runtime = CoderRuntime(config, service)
    if args.acknowledge:
        if not args.reconciled:
            raise WorkflowError(
                "operator must reconcile Git/PR/handoff first; use --reconciled"
            )
        # Keep the ledger slot held until Docker settlement is established.
        runtime.reconcile(args.acknowledge)
        ledger.acknowledge(args.acknowledge)
        return
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


if __name__ == "__main__":
    main()
