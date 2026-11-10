"""Trusted Docker phase orchestration and worker handoff validation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import time
import tempfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from agent_workflow.budget import check_deadline, cleanup_budget, operation_timeout
from agent_workflow.integrations.github import REPOSITORY, WorkflowError, canonical_repository
from agent_workflow.domain.service import WorkflowService
from agent_workflow.storage.dispatch_repository import DispatchLedger
from agent_workflow.runners.runtime_plugins import (
    AgentRuntimePlugin,
    CodexCLIPlugin,
    RuntimePlan,
    RuntimeRegistry,
    RuntimeRequest,
    RuntimeSpec,
)
from agent_workflow.runners.sandbox import (
    DockerCreateRejected,
    SandboxManager,
    SandboxPolicy,
    _DOCKER_HOST,
    _assert_daemon,
    _assert_selected_host,
    _bind_host,
    _docker_invocation,
    _environment,
    _require_engine,
    _selected_host,
    docker_security_args,
)

DENIED = {".git", ".secret", ".runtime", ".codex", ".env", "AGENTS.md"}
GENERATED = {".pytest_cache", "__pycache__"}
MAX_BYTES = 10_000_000
MAX_SNAPSHOT_FILES = 10_000
MAX_SNAPSHOT_FILE_BYTES = 16 * 1024 * 1024
MAX_SNAPSHOT_TOTAL_BYTES = 64 * 1024 * 1024
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
    if config.get("runtime", "codex") == "codex":
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
    """Hash a bounded snapshot without allocating file content on the host."""
    digest = hashlib.sha256()
    file_count = 0
    total_bytes = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        check_deadline()
        dirs.sort()
        for name in list(dirs):
            if is_linked(Path(directory) / name):
                raise WorkflowError("linked source directory")
            if name in GENERATED:
                dirs.remove(name)
        for name in sorted(files):
            check_deadline()
            path = Path(directory) / name
            info = path.lstat()
            if is_linked(path) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise WorkflowError("unsafe source file")
            file_count += 1
            total_bytes += info.st_size
            if (
                file_count > MAX_SNAPSHOT_FILES
                or info.st_size > MAX_SNAPSHOT_FILE_BYTES
                or total_bytes > MAX_SNAPSHOT_TOTAL_BYTES
            ):
                raise WorkflowError("source snapshot exceeds bounded file or byte budget")
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
                        raise WorkflowError("source file changed while hashing")
                    digest.update(chunk)
                    remaining -= len(chunk)
                if handle.read(1):
                    raise WorkflowError("source file grew while hashing")
            if path.stat().st_size != info.st_size:
                raise WorkflowError("source file size changed while hashing")
    return digest.hexdigest()

def docker_command(args: list[str]) -> bytes:
    if not args or args[0] != "docker":
        raise WorkflowError("Controller may only launch Docker")
    # Use fixed operation names only. Arguments and subprocess output can
    # contain host paths, environment values or untrusted content.
    operation = "command"
    for prefix, name in (
        (["info"], "info"),
        (["image", "inspect"], "image inspect"),
        (["create"], "create"),
        (["container", "inspect"], "container inspect"),
        (["container", "ls"], "container ls"),
        (["rm"], "rm"),
        (["volume", "inspect"], "volume inspect"),
    ):
        if args[1:1 + len(prefix)] == prefix:
            operation = name
            break

    timeout = operation_timeout(30)
    invocation, env = _docker_invocation(args)
    try:
        return subprocess.run(
            invocation, env=env, capture_output=True, check=True, timeout=timeout
        ).stdout
    except subprocess.TimeoutExpired:
        check_deadline()
        raise WorkflowError(f"Docker {operation} timed out") from None
    except subprocess.CalledProcessError as error:
        if operation == "create" and error.returncode == 1:
            # Only an exact daemon rejection of the requested immutable image
            # is terminal. Timeouts, transport errors and conflicts are not.
            rejection = (error.stderr or b"").strip()
            image = next((arg for arg in args if re.fullmatch(r"sha256:[0-9a-f]{64}", arg)), None)
            if image and rejection == b"Error response from daemon: No such image: " + image.encode("ascii"):
                raise DockerCreateRejected("Docker rejected the sandbox image") from None
        raise WorkflowError(
            f"Docker {operation} failed (exit {error.returncode})"
        ) from None
    except (FileNotFoundError, PermissionError):
        if operation == "create":
            raise DockerCreateRejected("could not launch Docker create") from None
        raise WorkflowError(f"could not launch Docker {operation}") from None
    except OSError:
        raise WorkflowError(f"Docker {operation} I/O failed") from None


def run_container(args: list[str], prompt: str, remaining: int) -> bytes:
    # Output goes to private bounded temporary storage, never Docker logs/chat.
    with tempfile.TemporaryFile() as output:
        invocation, env = _docker_invocation(args)
        process = subprocess.Popen(
            invocation, env=env, stdin=subprocess.PIPE, stdout=output, stderr=output
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
    def __init__(
        self,
        config: dict,
        service: WorkflowService,
        *,
        plugins: Iterable[AgentRuntimePlugin] = (),
    ) -> None:
        self.config, self.service = config, service
        self.registry = RuntimeRegistry((CodexCLIPlugin(), *plugins))
        database = config.get("database") or getattr(service.repository, "db_path", None)
        fence_dir = (
            Path(str(Path(database).resolve()) + ".sandbox-fences")
            if database is not None
            else None
        )
        self.sandbox = SandboxManager(fence_dir=fence_dir)

    def _code_request(self, claim: dict) -> RuntimeRequest:
        runtime = self.config.get("runtime", "codex")
        provider = self.config.get(
            "provider", CodexCLIPlugin.default_provider if runtime == "codex" else ""
        )
        return RuntimeRequest(
            spec=RuntimeSpec(
                runtime, provider, self.config["model"], self.config.get("effort")
            ),
            role="dev",
            phase="code",
            dispatch_id=claim["dispatch_id"],
            input_sha=claim["input_sha"],
            required_capabilities=frozenset({"workspace-write"}),
        )

    def _run_code(
        self,
        claim: dict,
        source: Path,
        request: RuntimeRequest,
        plan: RuntimePlan,
        prompt: str,
        approved_image_id: str | None,
    ) -> None:
        if approved_image_id is None:
            # Only the built-in Codex device-login profile is exempt from
            # strict sandbox image approval.
            output = self.docker(claim, source, "code", prompt, code_plan=plan)
            return self.registry.result(request, output)

        launch = self.sandbox.run(
            SandboxPolicy(timeout_seconds=self._remaining_seconds(claim)),
            dispatch_id=claim["dispatch_id"],
            workspace=source,
            image=self.config["runtime_image"],
            approved_image_id=approved_image_id,
            command=plan.command,
            env=plan.env,
            stdin=prompt,
        )
        return self.registry.result(request, launch.output, observed_image_id=launch.image_id)

    @staticmethod
    def _remaining_seconds(claim: dict) -> int:
        remaining = int(claim["deadline"] - time.time())
        if remaining <= 0:
            raise WorkflowError("dispatch deadline exceeded")
        remaining = int(operation_timeout(remaining))
        if remaining <= 0:
            raise WorkflowError("dispatch deadline exceeded")
        return remaining

    def reconcile(self, dispatch_id: str) -> None:
        if DispatchLedger(self.service).is_pre_launch(dispatch_id):
            if self.sandbox.has_launch_records(dispatch_id):
                raise WorkflowError("pre-launch evidence conflicts with Docker records")
            return
        host, daemon_id = self.sandbox.recovery_engine(dispatch_id)
        with cleanup_budget(), _bind_host(host):
            _assert_daemon(daemon_id)
            self.stop(dispatch_id)
            self.sandbox.stop(dispatch_id)
            _assert_daemon(daemon_id)

    def stop(self, dispatch_id: str) -> None:
        if not re.fullmatch(r"[a-f0-9]{32}", dispatch_id):
            raise WorkflowError("invalid dispatch identifier")
        host, daemon_id = self.sandbox.recovery_engine(dispatch_id)
        with cleanup_budget(), _bind_host(host):
            _assert_daemon(daemon_id)
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
            _assert_daemon(daemon_id)

    def docker(
        self,
        claim: dict,
        stage: Path,
        mode: str,
        prompt: str = "",
        *,
        code_plan: RuntimePlan | None = None,
    ) -> bytes:
        dispatch_id = claim["dispatch_id"]
        host = _DOCKER_HOST.get()
        if host is None:
            # The standalone probe/test entry point also needs a durable pin.
            existing = self.sandbox._engine_path(dispatch_id).exists()
            if existing and self.service is not None and DispatchLedger(self.service).is_pre_launch(dispatch_id):
                raise WorkflowError("pre-launch evidence conflicts with Docker records")
            host = self.sandbox.recovery_engine(dispatch_id)[0] if existing else _selected_host()
            with _bind_host(host):
                if not existing:
                    self._record_engine(dispatch_id, host, _require_engine())
                return self._docker_bound(claim, stage, mode, prompt, code_plan)
        return self._docker_bound(claim, stage, mode, prompt, code_plan)

    def _docker_bound(
        self, claim: dict, stage: Path, mode: str, prompt: str,
        code_plan: RuntimePlan | None,
    ) -> bytes:
        host, daemon_id = self.sandbox.recovery_engine(claim["dispatch_id"])
        _assert_selected_host(host)
        _assert_daemon(daemon_id)
        name = f"my-wfc-{claim['dispatch_id']}-{mode}"
        args = [
            "docker",
            "run",
            "--name",
            name,
            "--label",
            "my-wfc.phase=3.1",
            *docker_security_args(SandboxPolicy()),
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
        command = (mode,)
        if mode in {"code", "test", "probe"}:
            args += _environment(code_plan.env if mode == "code" and code_plan else ())
        if mode == "code":
            if (
                code_plan is None
                or code_plan.command != command
                or code_plan.env != (("WFC_CODEX_MODEL", self.config["model"]),)
            ):
                raise WorkflowError("legacy Codex profile requires the Codex code plan")
            volume = self.config.get("codex_auth_volume")
            if volume != "techlead-review-poc_codex_auth":
                raise WorkflowError("dedicated POC Codex auth volume required")
            docker_command(["docker", "volume", "inspect", volume])
            args += [
                "--mount",
                f"type=volume,src={volume},dst=/home/coder/.codex",
            ]
        elif mode not in {"prepare", "publish"}:
            args += ["--network=none"]

        remaining = self._remaining_seconds(claim)
        args += [
            "--env",
            "WFC_RUNTIME_SECONDS=" + str(remaining),
            self.config["image"],
            *command,
        ]
        return run_container(args, prompt, remaining)

    def execute(self, claim: dict, context: dict) -> str:
        request = self._code_request(claim)
        plan = self.registry.plan(request)
        approved_image_id = self.registry.approved_image_id(request)
        if approved_image_id is not None and not self.config.get("runtime_image"):
            raise WorkflowError("explicit sandbox image required for runtime plugin")
        host = _selected_host()
        with _bind_host(host):
            daemon_id = _require_engine()
            self._record_engine(claim["dispatch_id"], host, daemon_id)
            result = self._execute_bound(claim, context, request, plan, approved_image_id)
            _assert_daemon(daemon_id)
            return result

    def _record_engine(self, dispatch_id: str, host: str, daemon_id: str) -> None:
        if self.service is not None:
            DispatchLedger(self.service).retire_pre_launch(dispatch_id)
        self.sandbox.record_engine(dispatch_id, host, daemon_id)

    def release_engine(self, dispatch_id: str) -> None:
        """Drop the pin only after the dispatch ledger reaches a terminal state."""
        self.sandbox.clear_engine(dispatch_id)

    def _execute_bound(
        self, claim: dict, context: dict, request: RuntimeRequest, plan: RuntimePlan,
        approved_image_id: str | None,
    ) -> str:
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
            _, daemon_id = self.sandbox.recovery_engine(claim["dispatch_id"])
            _assert_daemon(daemon_id)
            runtime_result = self._run_code(
                claim, source, request, plan, prompt, approved_image_id
            )
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
            from agent_workflow.integrations.github import validate_sha

            validate_sha(result["head_sha"])
            for field in ("task_id", "repository", "branch", "base_sha"):
                if result[field] != context[field]:
                    raise WorkflowError(
                        "worker result differs from authorized task"
                    )
            if result["input_sha"] != claim["input_sha"]:
                raise WorkflowError("worker result input mismatch")
            check_deadline()
            DispatchLedger(self.service).record_runtime_evidence(
                claim["dispatch_id"], context["task_id"], claim["input_sha"],
                request.spec.runtime, runtime_result.declared_runtime_version,
                runtime_result.observed_image_id, runtime_result.telemetry,
                tested_digest, result["head_sha"],
            )
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
