"""Detached trusted host watchdog for strict offline sandbox deadlines.

Runs in a separate process/session, pinned to the original local Docker endpoint.
Never receives credentials or untrusted prompt content. Fail closed on engine drift.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import sys
import time

from agent_workflow.budget import dispatch_budget
from agent_workflow.integrations.github import WorkflowError
from agent_workflow.runners.sandbox import (
    SandboxManager, _assert_daemon, _bind_host, _dispatch_prefix, _sync_directory,
)


def enforce(scope: Path, dispatch_id: str) -> None:
    manager = SandboxManager(scope.parent)
    record = manager._scope_data(scope)
    with _bind_host(record["host"]):
        manager._assert_scope(scope, dispatch_id)
        _enforce_bound(manager, scope, dispatch_id, record)


def _enforce_bound(manager: SandboxManager, scope: Path, dispatch_id: str, record: dict) -> None:
    ready = scope.with_name(scope.name + ".ready")
    with ready.open("x", encoding="ascii") as handle:
        handle.write("watchdog-ready")
        handle.flush()
        os.fsync(handle.fileno())
    _sync_directory(ready.parent)

    while scope.exists():
        remaining = min(
            record["deadline"] - time.time(),
            record["monotonic_deadline"] - time.monotonic(),
        )
        if remaining > 0:
            time.sleep(min(remaining, 0.25))
            continue
        # The Controller might be gone; remove ONLY this exact owned sandbox.
        # Never clear the durable scope: Owner/Controller reconciliation must
        # confirm the same engine and global ledger before releasing dispatch.
        for attempt in range(3):
            if not scope.exists():
                return
            try:
                with dispatch_budget(time.time() + 5):
                    manager._assert_scope(scope, dispatch_id)
                    manager._cleanup(dispatch_id, re.escape(record["name"]))
                    _assert_daemon(record["daemon_id"])
                break
            except (OSError, WorkflowError):
                if attempt == 2:
                    # Retain the durable scope for operator reconciliation.
                    return
                time.sleep(0.25 * (attempt + 1))
        if not scope.exists():
            return
        stamp = scope.with_name(scope.name + ".expired")
        with stamp.open("w", encoding="ascii") as handle:
            handle.write("deadline-cleanup-confirmed")
            handle.flush()
            os.fsync(handle.fileno())
        _sync_directory(stamp.parent)
        return


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(2)
    scope = Path(sys.argv[1])
    dispatch_id = sys.argv[2]
    _dispatch_prefix(dispatch_id)
    if not scope.is_absolute() or not scope.parent.is_dir():
        raise SystemExit(2)
    enforce(scope, dispatch_id)


if __name__ == "__main__":
    main()
