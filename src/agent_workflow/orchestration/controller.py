"""Durable single-slot dispatch. Uncertain executions require reconciliation."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from agent_workflow.app_config import AppConfig
from agent_workflow.budget import dispatch_budget
from agent_workflow.domain.service import WorkflowService
from agent_workflow.infrastructure.logging.json_formatter import ci_log_scope
from agent_workflow.integrations.github import WorkflowError
from agent_workflow.interfaces.cli import ci_controller as ci_cli
from agent_workflow.interfaces.cli import dispatch_controller as dispatch_cli
from agent_workflow.runners.coder_runtime import LegacySqliteCoderRuntime
from agent_workflow.storage.dispatch_repository import DispatchLedger


def run_once(
    service: WorkflowService,
    ledger: DispatchLedger,
    task_ids: set[str],
    execute: Callable[[dict, dict], str],
    timeout: int = 1800,
    *,
    track_pre_launch: bool = False,
    on_finished: Callable[[str], None] | None = None,
) -> bool:
    claim = ledger.claim(task_ids, timeout, track_pre_launch=track_pre_launch)
    if claim is None:
        return False
    try:
        with dispatch_budget(claim["deadline"]):
            context = service.get_coder_assignment(claim["task_id"])
            if (
                context["completed_review_rounds"] != claim["round_number"]
                or context["status"] not in {"AWAITING_HANDOFF", "CHANGES_REQUESTED"}
                or (context["current_head_sha"] or context["base_sha"])
                != claim["input_sha"]
            ):
                raise WorkflowError("assignment changed after dispatch")
            sha = execute(claim, context)
            ledger.finish(claim["dispatch_id"], sha)
    except BaseException:
        ledger.block(claim["dispatch_id"])
        raise
    if on_finished is not None:
        on_finished(claim["dispatch_id"])
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
    dispatch_cli.add_dispatch_argument(parser)
    parser.add_argument("--acknowledge", metavar="DISPATCH_ID")
    parser.add_argument("--reconciled", action="store_true")
    ci_cli.add_ci_arguments(parser)
    args = parser.parse_args()
    if args.ci_once or args.ci_watch:
        from agent_workflow.composition.postgres_ci_entrypoint import (
            compose_ci_execution,
        )

        ci_cli.run_ci_command(
            ci_once=args.ci_once,
            ci_watch=args.ci_watch,
            dispatch_once=args.once,
            acknowledge=args.acknowledge,
            reconciled=args.reconciled,
            prepare=lambda: (
                json.loads(
                    args.config.resolve(strict=True).read_text(encoding="utf-8-sig")
                ),
                AppConfig.from_env(),
            ),
            compose=compose_ci_execution,
            log_scope=ci_log_scope,
        )
        return
    postgres = "PG_RUNTIME_DSN" in os.environ
    config = None if postgres else dispatch_cli.read_controller_config(args.config)
    # With no PostgreSQL selection, legacy one-shot callers supply a database.
    if postgres or (
        args.once and (not isinstance(config, dict) or "database" not in config)
    ):
        from agent_workflow.composition.postgres_dispatch_entrypoint import (
            compose_dispatch_execution,
        )

        dispatch_cli.run_dispatch_command(
            once=args.once,
            acknowledge=args.acknowledge,
            reconciled=args.reconciled,
            prepare=lambda: (
                dispatch_cli.read_controller_config(args.config) if postgres else config
            ),
            compose=compose_dispatch_execution,
        )
        return
    from agent_workflow.runners.coder_runtime import validate_config

    validate_config(config)
    with process_lock(Path(config["database"]).with_suffix(".controller.lock")):
        operate(config, args)


def operate(config: dict, args: argparse.Namespace) -> None:
    if getattr(args, "ci_once", False) or getattr(args, "ci_watch", False):
        raise WorkflowError("CI-only modes require the PostgreSQL CLI entrypoint")
    from agent_workflow.runners.coder_runtime import CoderRuntime

    # Read validated configuration at the operational composition boundary.
    service = WorkflowService(
        config["database"], require_ci=AppConfig.from_env().ci_gate_required
    )
    ledger = DispatchLedger(service)
    runtime: CoderRuntime = LegacySqliteCoderRuntime(config, service)
    if args.acknowledge:
        if not args.reconciled:
            raise WorkflowError(
                "operator must reconcile Git/PR/handoff first; use --reconciled"
            )
        # Keep the ledger slot held until Docker settlement is established.
        runtime.reconcile(args.acknowledge)
        ledger.acknowledge(args.acknowledge)
        runtime.release_engine(args.acknowledge)
        return
    while True:
        try:
            run_once(
                service,
                ledger,
                set(config["tasks"]),
                runtime.execute,
                config.get("timeout_seconds", 1800),
                track_pre_launch=True,
                on_finished=runtime.release_engine,
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
