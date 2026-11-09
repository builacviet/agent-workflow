"""Detached trusted host watchdog for strict offline sandbox deadlines.

Runs in a separate process/session, pinned to the launching Docker client context.
Never receives credentials or untrusted prompt content. Fail closed on engine drift.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import sys
import time

from agent_workflow.integrations.github import WorkflowError
from agent_workflow.runners.sandbox import (
    SandboxManager, _dispatch_prefix, _require_engine, _sync_directory,
)


def enforce(scope: Path, dispatch_id: str) -> None:
    manager = SandboxManager(scope.parent)
    record = manager._scope_data(scope)
    if (
        record["dispatch_id"] != dispatch_id
        or _require_engine() != record["daemon_id"]
    ):
        raise WorkflowError("watchdog Docker engine differs from durable scope")
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
        if _require_engine() != record["daemon_id"]:
            raise WorkflowError("watchdog cannot reconcile against another daemon")
        manager._cleanup(dispatch_id, re.escape(record["name"]))
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
