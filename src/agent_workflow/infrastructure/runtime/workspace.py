"""Layer: Infrastructure
Responsibility: Prepare runtime workspaces and validate source snapshots and Worker results.
Dependencies: Application runtime contracts, Domain SHA validation and filesystem APIs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path

from agent_workflow.application.dispatch.ports import DispatchClaim
from agent_workflow.application.exceptions import RuntimeContractError
from agent_workflow.application.runtime.ports import PublishedResult, WorkerEnvelope
from agent_workflow.budget import check_deadline
from agent_workflow.domain.exceptions import DomainError
from agent_workflow.domain.workflow_identity import REPOSITORY, validate_sha

GENERATED = {".pytest_cache", "__pycache__"}
MAX_SNAPSHOT_FILES = 10_000
MAX_SNAPSHOT_FILE_BYTES = 16 * 1024 * 1024
MAX_SNAPSHOT_TOTAL_BYTES = 64 * 1024 * 1024


def is_linked(path: Path) -> bool:
    info = path.lstat()
    return path.is_symlink() or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def source_digest(root: Path) -> str:
    """Hash a bounded snapshot without allocating file content on the host."""
    digest = hashlib.sha256()
    file_count = 0
    total_bytes = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        check_deadline()
        dirs.sort()
        for name in list(dirs):
            if is_linked(Path(directory) / name):
                raise RuntimeContractError("linked source directory")
            if name in GENERATED:
                dirs.remove(name)
        for name in sorted(files):
            check_deadline()
            path = Path(directory) / name
            info = path.lstat()
            if is_linked(path) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise RuntimeContractError("unsafe source file")
            file_count += 1
            total_bytes += info.st_size
            if (
                file_count > MAX_SNAPSHOT_FILES
                or info.st_size > MAX_SNAPSHOT_FILE_BYTES
                or total_bytes > MAX_SNAPSHOT_TOTAL_BYTES
            ):
                raise RuntimeContractError(
                    "source snapshot exceeds bounded file or byte budget"
                )
            name_bytes = path.relative_to(root).as_posix().encode("utf-8")
            digest.update(len(name_bytes).to_bytes(8, "big"))
            digest.update(name_bytes)
            digest.update(info.st_size.to_bytes(8, "big"))
            with path.open("rb") as handle:
                remaining = info.st_size
                while remaining:
                    check_deadline()
                    chunk = handle.read(min(65536, remaining))
                    if not chunk:
                        raise RuntimeContractError("source file changed while hashing")
                    digest.update(chunk)
                    remaining -= len(chunk)
                if handle.read(1):
                    raise RuntimeContractError("source file grew while hashing")
            if path.stat().st_size != info.st_size:
                raise RuntimeContractError("source file size changed while hashing")
    return digest.hexdigest()


class RuntimeWorkspace:
    def __init__(self, config: dict) -> None:
        self.config = config

    source_digest = staticmethod(source_digest)

    def prepare_stage(
        self, claim: DispatchClaim, context: dict
    ) -> tuple[Path, WorkerEnvelope]:
        task = self.config["tasks"][context["task_id"]]
        stage = Path(task["worktree"]) / ".runtime" / "dispatch" / claim.dispatch_id
        root = Path(task["worktree"]).resolve(strict=True)
        if not stage.resolve().is_relative_to(root):
            raise RuntimeContractError("dispatch path escapes allowed worktree")
        for parent in stage.parents:
            if parent == root:
                break
            if parent.exists() and is_linked(parent):
                raise RuntimeContractError("linked dispatch parent")
        stage.mkdir(parents=True, mode=0o700)
        stage.chmod(0o777)
        envelope: WorkerEnvelope = {
            "context": context,
            "input_sha": claim.input_sha,
            "deadline": claim.deadline,
            "write_paths": task["write_paths"],
            "handoff_path": task["handoff_path"],
            "app_id": self.config["app_id"],
            "installation_id": self.config["installation_id"],
        }
        return stage, envelope

    @staticmethod
    def source(stage: Path) -> Path:
        source = stage / "source"
        if not source.is_dir() or is_linked(source):
            raise RuntimeContractError("worker source missing or linked")
        return source

    @staticmethod
    def check_source_digest(source: Path, expected: str) -> None:
        if source_digest(source) != expected:
            raise RuntimeContractError("test phase mutated source")

    def validated_result(
        self, stage: Path, claim: DispatchClaim, context: dict
    ) -> PublishedResult:
        result_path = stage / "result.json"
        if is_linked(result_path) or result_path.stat().st_size > 65536:
            raise RuntimeContractError("unsafe worker result")
        result = json.loads(result_path.read_text())
        required = {
            "task_id",
            "repository",
            "branch",
            "base_sha",
            "head_sha",
            "input_sha",
            "pr_url",
        }
        if not isinstance(result, dict) or not required.issubset(result):
            raise RuntimeContractError("incomplete worker result")
        if not isinstance(result["pr_url"], str) or not re.fullmatch(
            rf"https://github\.com/{re.escape(REPOSITORY)}/pull/[1-9][0-9]*",
            result["pr_url"],
        ):
            raise RuntimeContractError("worker PR reference differs from repository")
        try:
            validate_sha(result["head_sha"])
        except DomainError:
            raise RuntimeContractError(
                "a full lowercase 40-character commit SHA is required"
            ) from None
        for field in ("task_id", "repository", "branch", "base_sha"):
            if result[field] != context[field]:
                raise RuntimeContractError("worker result differs from authorized task")
        if result["input_sha"] != claim.input_sha:
            raise RuntimeContractError("worker result input mismatch")
        return PublishedResult(result["head_sha"], result["pr_url"])
