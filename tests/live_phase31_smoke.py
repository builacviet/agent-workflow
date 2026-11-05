"""Opt-in real Docker verification. No GitHub/OpenAI credentials or v2 resources."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import stat
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from my_wfc.coder_runtime import CoderRuntime, safe_path
from coder_worker.repository import git
from my_wfc.github import WorkflowError
from my_wfc.service import WorkflowService


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="my-wfc-codex:phase31")
    args = parser.parse_args()
    scratch = ROOT / ".runtime" / "verification" / uuid.uuid4().hex
    stage = scratch / "source"
    stage.mkdir(parents=True)
    # Explicit Git inventory; ignored secrets/data are never read or mounted.
    inventory = git(
        ROOT, "ls-files", "-z", "--cached", "--others", "--exclude-standard"
    )
    for raw in inventory.split(b"\0"):
        if not raw:
            continue
        relative = raw.decode()
        template = (
            relative == ".env.example"
            or relative.startswith(".runtime/")
            and relative.endswith(".example")
        )
        if relative != "AGENTS.md" and not template:
            safe_path(relative)
        source = ROOT / relative
        if not source.exists() and not source.is_symlink():
            continue  # tracked deletion in an uncommitted refactor
        if not stat.S_ISREG(source.lstat().st_mode):
            raise WorkflowError(
                "smoke source contains a linked/nonregular file"
            )
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    for directory, _, names in os.walk(stage):
        Path(directory).chmod(0o777)
        for name in names:
            (Path(directory) / name).chmod(0o666)
    profile = ROOT / "docker/coder-seccomp.json"
    runtime = CoderRuntime(
        {
            "image": args.image,
            "seccomp_profile": str(profile),
            "seccomp_sha256": hashlib.sha256(profile.read_bytes()).hexdigest(),
        },
        WorkflowService(scratch / "smoke.sqlite3"),
    )
    claim = {"dispatch_id": uuid.uuid4().hex, "deadline": time.time() + 120}
    try:
        print(runtime.docker(claim, stage, "probe").decode(), flush=True)
        runtime.stop(claim["dispatch_id"])
        print(runtime.docker(claim, stage, "test").decode(), flush=True)
    finally:
        runtime.stop(claim["dispatch_id"])
    timeout_stage = scratch / "timeout"
    timeout_stage.mkdir()
    timeout_stage.chmod(0o777)
    (timeout_stage / "test_timeout.py").write_text(
        "def test_sleep():\n    import time\n    time.sleep(30)\n"
    )
    claim = {"dispatch_id": uuid.uuid4().hex, "deadline": time.time() + 3}
    started = time.monotonic()
    try:
        try:
            runtime.docker(claim, timeout_stage, "test")
        except WorkflowError:
            pass
        else:
            raise AssertionError("timeout probe unexpectedly completed")
    finally:
        runtime.stop(claim["dispatch_id"])
    print(f"TIMEOUT_STOP_CONFIRMED ({time.monotonic() - started:.2f}s)")


if __name__ == "__main__":
    main()
