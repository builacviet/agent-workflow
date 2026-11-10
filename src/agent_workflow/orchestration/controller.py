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
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--acknowledge", metavar="DISPATCH_ID")
    parser.add_argument("--reconciled", action="store_true")
    parser.add_argument("--ci-once", action="store_true", help="reconcile CI only, never dispatch agents")
    parser.add_argument("--ci-watch", action="store_true", help="consume local inbox, never dispatch agents")
    args = parser.parse_args()
    config = json.loads(
        args.config.resolve(strict=True).read_text(encoding="utf-8-sig")
    )
    from agent_workflow.runners.coder_runtime import validate_config

    if not (args.ci_once or args.ci_watch):
        validate_config(config)
    elif args.ci_once and args.ci_watch:
        raise WorkflowError("choose one CI-only mode")
    # CI Watcher and Dev Controller use DIFFERENT PostgreSQL session locks.
    # No production cutover occurs simply by installing the optional driver.
    ci_mode = args.ci_once or args.ci_watch
    dsn_key = (
        "AGENT_WORKFLOW_CI_DATABASE_URL" if ci_mode
        else "AGENT_WORKFLOW_CONTROLLER_DATABASE_URL"
    )
    pg_url = os.environ.get(dsn_key)
    if pg_url:
        if not pg_url.startswith(("postgresql://", "postgres://")):
            raise WorkflowError("PostgreSQL Controller database URL required")
        config = {**config, "database": pg_url}
        from agent_workflow.storage.postgres_locks import controller_advisory_lock
        role = "ci_watcher" if ci_mode else "dev_controller"
        with controller_advisory_lock(pg_url, role) as lease:
            lease.ensure_alive()
            operate(config, args, lock_lease=lease)
    else:
        with process_lock(Path(config["database"]).with_suffix(".controller.lock")):
            operate(config, args)


def operate(config: dict, args: argparse.Namespace, *, lock_lease=None) -> None:
    if getattr(args, "ci_once", False) or getattr(args, "ci_watch", False):
        return _ci_operator(config, watch=bool(getattr(args, "ci_watch", False)),
                            lock_lease=lock_lease)
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
        runtime.release_engine(args.acknowledge)
        return
    while True:
        try:
            if lock_lease is not None:
                lock_lease.ensure_alive()
            def execute_with_lease(claim, context):
                if lock_lease is not None:
                    lock_lease.ensure_alive()
                result = runtime.execute(claim, context)
                if lock_lease is not None:
                    lock_lease.ensure_alive()
                return result
            run_once(
                service,
                ledger,
                set(config["tasks"]),
                execute_with_lease,
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


def _ci_operator(config: dict, *, watch: bool, lock_lease=None) -> None:
    """Explicit CI-only path: cannot launch Codex or touch worker credentials."""
    from agent_workflow.integrations.ci_controller import ControllerCiCoordinator
    from agent_workflow.integrations.github_app_checks import (
        ControllerAppToken, ControllerChecksClient,
    )
    database = config["database"]
    if database.startswith(("postgresql://", "postgres://")):
        from agent_workflow.storage.postgres_ci_state import PostgresControllerCiState
        ci_state = PostgresControllerCiState(database)
    else:
        from agent_workflow.storage.ci_state import ControllerCiState
        ci_state = ControllerCiState(database)

    gate = config.get("controller_github_app")
    if not isinstance(gate, dict):
        raise WorkflowError("separate read-only Controller GitHub App config required")
    app_id, installation_id, pem = (
        gate.get("app_id"), gate.get("installation_id"), gate.get("pem"),
    )
    if type(app_id) is not int or type(installation_id) is not int or not pem:
        raise WorkflowError("invalid Controller GitHub App config")
    if config.get("pem") and Path(config["pem"]).resolve() == Path(pem).resolve():
        raise WorkflowError("Controller GitHub App must not use worker App PEM")
    token = ControllerAppToken(
        app_id=app_id, installation_id=installation_id, pem_path=pem,
    )
    checks = ControllerChecksClient(
        installation_id=installation_id, token_provider=token,
    )
    ci = ControllerCiCoordinator(
        ci_state, checks, installation_id=installation_id,
    )
    if lock_lease is not None:
        lock_lease.ensure_alive()
    observations = ci.reconcile_once(startup=True)
    print("CI reconciliation: " + str(len(observations)) + " tracked observations", flush=True)
    if not watch:
        return
    delay = 2
    while True:
        # Loss of the session lock must terminate, not silently run as a
        # second watcher after PostgreSQL has released the previous lock.
        if lock_lease is not None:
            lock_lease.ensure_alive()
        try:
            observations = ci.reconcile_once()
            if any(e.reason == "GITHUB_READ_UNAVAILABLE" for e in observations):
                delay = min(60, delay * 2)
            else:
                delay = 2
        except (WorkflowError, OSError, sqlite3.Error):
            print("CI reconciliation blocked; inbox retained", flush=True)
            delay = min(60, delay * 2)
        time.sleep(delay)


if __name__ == "__main__":
    main()
