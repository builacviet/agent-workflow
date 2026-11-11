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
    with process_lock(
        Path(config["database"]).with_suffix(".controller.lock")
    ):
        operate(config, args)


def operate(config: dict, args: argparse.Namespace) -> None:
    if getattr(args, "ci_once", False) or getattr(args, "ci_watch", False):
        return _ci_operator(config, watch=bool(getattr(args, "ci_watch", False)))
    from agent_workflow.runners.coder_runtime import CoderRuntime

    # Operational Controller must never inherit the optional legacy CI env flag.
    service = WorkflowService(config["database"], require_ci=True)
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


def _ci_operator(config: dict, *, watch: bool) -> None:
    """Explicit CI-only path: cannot launch Codex or touch worker credentials."""
    from agent_workflow.integrations.ci_controller import ControllerCiCoordinator
    from agent_workflow.integrations.github_app_checks import (
        ControllerAppToken, ControllerChecksClient,
    )
    from agent_workflow.storage.ci_state import ControllerCiState

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
        ControllerCiState(config["database"]), checks, installation_id=installation_id,
    )
    observations = ci.reconcile_once(startup=True)
    print("CI reconciliation: " + str(len(observations)) + " tracked observations", flush=True)
    if not watch:
        return
    delay = 2
    while True:
        try:
            observations = ci.reconcile_once()
            # A retained BLOCKED inbox may require multiple GitHub reads.
            # Bound retries for missing/pending checks as well as API errors.
            if any(e.verdict == "BLOCKED" for e in observations):
                delay = min(60, delay * 2)
            else:
                delay = 2
        except (WorkflowError, OSError, sqlite3.Error):
            print("CI reconciliation blocked; inbox retained", flush=True)
            delay = min(60, delay * 2)
        time.sleep(delay)


if __name__ == "__main__":
    main()
