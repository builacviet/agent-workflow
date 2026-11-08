"""Docker-only Controller adapter; no Git/GitHub operations or credentials."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import time
import tempfile
from pathlib import Path, PurePosixPath

from agent_workflow.budget import check_deadline, cleanup_budget, operation_timeout
from agent_workflow.github import REPOSITORY, WorkflowError, canonical_repository
from agent_workflow.service import WorkflowService

DENIED = {".git", ".secret", ".runtime", ".codex", ".env", "AGENTS.md"}
GENERATED = {".pytest_cache", "__pycache__"}
MAX_BYTES = 10_000_000
WINDOWS_DEVICES = {"con", "prn", "aux", "nul"} | {
    f"{prefix}{number}" for prefix in ("com", "lpt") for number in range(1, 10)
}


def is_linked(path: Path) -> bool:
    info = path.lstat()
    return path.is_symlink() or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def safe_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or "\\" in value
        or ":" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or any(
            part.casefold() in {name.casefold() for name in DENIED}
            or part.casefold().startswith(".env")
            or part.endswith((" ", "."))
            or part.split(".")[0].casefold() in WINDOWS_DEVICES
            for part in path.parts
        )
        or path.suffix.lower() in {".pem", ".key"}
    ):
        raise WorkflowError("unsafe coder path")
    return value


def writable(path: str, prefixes: list[str]) -> bool:
    safe_path(path)
    return any(
        path == prefix or path.startswith(prefix.rstrip("/") + "/")
        for prefix in prefixes
    )


def independent_path(path: Path, *, directory: bool = False) -> None:
    forbidden = (
        "agent_workflow_v2",
        "workflow_console_v2",
        "workflow-console-v2",
    )
    for candidate in (path, path.resolve()):
        directories = candidate.parts if directory else candidate.parts[:-1]
        if any(part.casefold().startswith(forbidden) for part in directories):
            raise WorkflowError("v2 paths are forbidden")


def validate_config(config: dict) -> None:
    if (
        canonical_repository(config.get("repository", "")) != REPOSITORY
        or not config.get("tasks")
    ):
        raise WorkflowError("explicit repository and task allowlist required")
    for key in ("database", "pem", "scratch"):
        path = Path(config[key])
        if not path.is_absolute():
            raise WorkflowError("absolute operator paths required")
        independent_path(path, directory=key == "scratch")
    profile = Path(config["seccomp_profile"])
    if (
        not profile.is_absolute()
        or not profile.is_file()
        or profile.is_symlink()
    ):
        raise WorkflowError("absolute independent seccomp profile required")
    independent_path(profile)
    if (
        hashlib.sha256(profile.read_bytes()).hexdigest()
        != config["seccomp_sha256"]
    ):
        raise WorkflowError("seccomp profile digest mismatch")
    if config.get("codex_auth_volume") != "techlead-review-poc_codex_auth":
        raise WorkflowError("dedicated POC Codex auth volume required")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/@:-]*", config["image"]):
        raise WorkflowError("invalid runtime image")
    if not isinstance(config.get("model"), str) or not config["model"].strip():
        raise WorkflowError("operator-selected model required")
    roots = set()
    for task in config["tasks"].values():
        root = Path(task["worktree"])
        if not root.is_absolute() or is_linked(root) or not root.is_dir():
            raise WorkflowError("dedicated canonical worktree required")
        if str(root.resolve()) != str(root) or root.resolve() in roots:
            raise WorkflowError("duplicate or noncanonical worktree")
        if not (root / ".git").is_file():
            raise WorkflowError("task requires linked Git worktree")
        independent_path(root, directory=True)
        roots.add(root.resolve())
        if not task.get("write_paths"):
            raise WorkflowError("explicit write path allowlist required")
        for prefix in task["write_paths"]:
            safe_path(prefix)
    if (
        type(config.get("timeout_seconds", 1800)) is not int
        or not 60 <= config.get("timeout_seconds", 1800) <= 3600
    ):
        raise WorkflowError("invalid timeout")


def source_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        for name in list(dirs):
            if is_linked(Path(directory) / name):
                raise WorkflowError("linked source directory")
            if name in GENERATED:
                dirs.remove(name)
        for name in sorted(files):
            path = Path(directory) / name
            if is_linked(path) or not path.is_file():
                raise WorkflowError("unsafe source file")
            name_bytes = path.relative_to(root).as_posix().encode("utf-8")
            content = path.read_bytes()
            digest.update(len(name_bytes).to_bytes(8, "big"))
            digest.update(name_bytes)
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
    return digest.hexdigest()


def docker_command(args: list[str]) -> bytes:
    if not args or args[0] != "docker":
        raise WorkflowError("Controller may only launch Docker")
    try:
        return subprocess.run(
            args,
            capture_output=True,
            check=True,
            timeout=operation_timeout(30),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        raise WorkflowError("Docker command failed") from None


def run_container(args: list[str], prompt: str, remaining: int) -> bytes:
    # Output goes to private bounded temporary storage, never Docker logs/chat.
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=output, stderr=output
        )
        try:
            started = time.monotonic()
            payload = prompt.encode()
            while True:
                try:
                    process.communicate(
                        payload, timeout=operation_timeout(min(1, remaining))
                    )
                    break
                except subprocess.TimeoutExpired:
                    payload = None
                    if (
                        time.monotonic() - started >= remaining
                        or os.fstat(output.fileno()).st_size > 4_000_000
                    ):
                        raise WorkflowError(
                            "runtime deadline or output budget exceeded"
                        )
            check_deadline()
            size = output.tell()
            if process.returncode or size > 4_000_000:
                raise WorkflowError(
                    "coder/test container failed or output exceeded limit"
                )
            output.seek(0)
            return output.read()
        except BaseException:
            process.kill()
            process.wait(timeout=10)
            raise


class CoderRuntime:
    def __init__(self, config: dict, service: WorkflowService) -> None:
        self.config, self.service = config, service

    def stop(self, dispatch_id: str) -> None:
        if not re.fullmatch(r"[a-f0-9]{32}", dispatch_id):
            raise WorkflowError("invalid dispatch identifier")
        with cleanup_budget():
            for suffix in ("code", "test", "probe", "prepare", "publish"):
                name = f"my-wfc-{dispatch_id}-{suffix}"
                # docker rm -f is scoped to our exact persisted names. No v2 operations.
                found = docker_command(
                    [
                        "docker",
                        "container",
                        "ls",
                        "-aq",
                        "--filter",
                        f"name=^/{name}$",
                    ]
                )
                if found.strip():
                    docker_command(["docker", "rm", "-f", name])
                if docker_command(
                    [
                        "docker",
                        "container",
                        "ls",
                        "-aq",
                        "--filter",
                        f"name=^/{name}$",
                    ]
                ).strip():
                    raise WorkflowError("runtime termination not confirmed")

    def docker(
        self, claim: dict, stage: Path, mode: str, prompt: str = ""
    ) -> bytes:
        name = f"my-wfc-{claim['dispatch_id']}-{mode}"
        args = [
            "docker",
            "run",
            "--name",
            name,
            "--label",
            "my-wfc.phase=3.1",
            "--read-only",
            "--user",
            "10001:10001",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--pids-limit=256",
            "--memory=2g",
            "--cpus=2",
            "--log-driver=none",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=512m,mode=1777",
            "--mount",
            f"type=bind,src={stage},dst=/workspace",
            "-i",
        ]
        if mode in {"code", "probe"}:
            profile = Path(self.config["seccomp_profile"]).resolve(strict=True)
            if (
                hashlib.sha256(profile.read_bytes()).hexdigest()
                != self.config["seccomp_sha256"]
            ):
                raise WorkflowError("seccomp profile changed")
            args += [
                "--security-opt",
                "seccomp=" + str(profile),
                "--tmpfs",
                "/home/coder:rw,nosuid,nodev,size=128m,uid=10001,gid=10001,mode=0700",
            ]
        if mode in {"prepare", "publish"}:
            key = Path(self.config["pem"]).resolve(strict=True)
            args += [
                "--mount",
                f"type=bind,src={key},dst=/run/.secret/coder-app.pem,readonly",
            ]
        if mode == "code":
            volume = self.config.get("codex_auth_volume")
            if volume != "techlead-review-poc_codex_auth":
                raise WorkflowError("dedicated POC Codex auth volume required")
            docker_command(["docker", "volume", "inspect", volume])
            args += [
                "--mount",
                f"type=volume,src={volume},dst=/home/coder/.codex",
                "--env",
                "WFC_CODEX_MODEL=" + self.config["model"],
            ]
        elif mode not in {"prepare", "publish"}:
            args += ["--network=none"]

        remaining = int(claim["deadline"] - time.time())
        if remaining <= 0:
            raise WorkflowError("dispatch deadline exceeded")
        remaining = int(operation_timeout(remaining))
        if remaining <= 0:
            raise WorkflowError("dispatch deadline exceeded")
        args += [
            "--env",
            "WFC_RUNTIME_SECONDS=" + str(remaining),
            self.config["image"],
            mode,
        ]
        return run_container(args, prompt, remaining)

    def execute(self, claim: dict, context: dict) -> str:
        task = self.config["tasks"][context["task_id"]]
        stage = (
            Path(task["worktree"])
            / ".runtime"
            / "dispatch"
            / claim["dispatch_id"]
        )
        root = Path(task["worktree"]).resolve(strict=True)
        if not stage.resolve().is_relative_to(root):
            raise WorkflowError("dispatch path escapes allowed worktree")
        for parent in stage.parents:
            if parent == root:
                break
            if parent.exists() and is_linked(parent):
                raise WorkflowError("linked dispatch parent")
        stage.mkdir(parents=True, mode=0o700)
        stage.chmod(0o777)
        envelope = {
            "context": context,
            "input_sha": claim["input_sha"],
            "deadline": claim["deadline"],
            "write_paths": task["write_paths"],
            "handoff_path": task["handoff_path"],
            "app_id": self.config["app_id"],
            "installation_id": self.config["installation_id"],
        }
        try:
            self.docker(claim, stage, "prepare", json.dumps(envelope))
            self.stop(claim["dispatch_id"])
            source = stage / "source"
            if not source.is_dir() or is_linked(source):
                raise WorkflowError("worker source missing or linked")
            prompt = (
                "Implement only this authorized task; do not commit/push/approve.\n"
                + json.dumps(context)
            )
            self.docker(claim, source, "code", prompt)
            self.stop(claim["dispatch_id"])
            tested_digest = source_digest(source)
            self.docker(claim, source, "test")
            self.stop(claim["dispatch_id"])
            if source_digest(source) != tested_digest:
                raise WorkflowError("test phase mutated source")
            envelope["test_summary"] = (
                "python -m pytest -q: exit 0 in credential-free network-disabled test container"
            )
            self.docker(claim, stage, "publish", json.dumps(envelope))
            self.stop(claim["dispatch_id"])
            result_path = stage / "result.json"
            if is_linked(result_path) or result_path.stat().st_size > 65536:
                raise WorkflowError("unsafe worker result")
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
                raise WorkflowError("incomplete worker result")
            if not isinstance(result["pr_url"], str) or not re.fullmatch(
                rf"https://github\.com/{re.escape(REPOSITORY)}/pull/[1-9][0-9]*",
                result["pr_url"],
            ):
                raise WorkflowError(
                    "worker PR reference differs from repository"
                )
            from agent_workflow.github import validate_sha

            validate_sha(result["head_sha"])
            for field in ("task_id", "repository", "branch", "base_sha"):
                if result[field] != context[field]:
                    raise WorkflowError(
                        "worker result differs from authorized task"
                    )
            if result["input_sha"] != claim["input_sha"]:
                raise WorkflowError("worker result input mismatch")
            check_deadline()
            self.service.register_task_handoff(
                context["task_id"],
                context["repository"],
                context["branch"],
                context["base_sha"],
                result["head_sha"],
                task["handoff_path"],
                envelope["test_summary"] + "; worker PR: " + result["pr_url"],
            )
            return result["head_sha"]
        finally:
            self.stop(claim["dispatch_id"])
