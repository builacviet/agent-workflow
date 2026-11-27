"""Layer: Interfaces/Worker
Responsibility: Orchestrate trusted Docker phases and validate worker handoffs.
Dependencies: Application handoff operation and runtime adapters.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from agent_workflow.application.dispatch.ports import DispatchClaim
from agent_workflow.application.runtime.dtos import (
    RuntimePlan,
    RuntimeRequest,
    WorkerResult,
)
from agent_workflow.application.runtime.ports import ICiTracking, IDispatchEvidence
from agent_workflow.application.runtime.services.agent_execution import (
    AgentExecutionService,
)
from agent_workflow.application.task.use_cases.get_coder_assignment import (
    DispatchAssignment,
)
from agent_workflow.application.task.use_cases.register_task_handoff import (
    HandoffUseCaseProvider,
)
from agent_workflow.budget import check_deadline, cleanup_budget, operation_timeout
from agent_workflow.domain.exceptions import BusinessRuleError
from agent_workflow.domain.task.handoff import HandoffRequest
from agent_workflow.domain.workflow_identity import REPOSITORY, validate_sha
from agent_workflow.infrastructure.runtime import sandbox
from agent_workflow.infrastructure.runtime.registry import (
    RuntimeRegistry,
    runtime_spec_from_config,
)
from agent_workflow.infrastructure.runtime.sandbox import (
    _DOCKER_HOST,
    SandboxManager,
    SandboxPolicy,
    _assert_daemon,
    _assert_selected_host,
    _bind_host,
    _environment,
    _require_engine,
    _selected_host,
    docker_security_args,
)
from agent_workflow.infrastructure.runtime.workspace import is_linked
from agent_workflow.interfaces.review_assignment_response import (
    coder_assignment_response,
)
from agent_workflow.sandbox_state_paths import validate_sandbox_state_dir

DENIED = {".git", ".secret", ".runtime", ".codex", ".env", "AGENTS.md"}
GENERATED = {".pytest_cache", "__pycache__"}
MAX_BYTES = 10_000_000
MAX_SNAPSHOT_FILES = 10_000
MAX_SNAPSHOT_FILE_BYTES = 16 * 1024 * 1024
MAX_SNAPSHOT_TOTAL_BYTES = 64 * 1024 * 1024
WINDOWS_DEVICES = {"con", "prn", "aux", "nul"} | {
    f"{prefix}{number}" for prefix in ("com", "lpt") for number in range(1, 10)
}


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
        raise BusinessRuleError("unsafe coder path")
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
            raise BusinessRuleError("v2 paths are forbidden")


def validate_config(config: dict, *, require_database: bool = True) -> None:
    if config.get("repository") != REPOSITORY or not config.get("tasks"):
        raise BusinessRuleError("explicit repository and task allowlist required")
    for key in (
        ("database", "pem", "scratch") if require_database else ("pem", "scratch")
    ):
        path = Path(config[key])
        if not path.is_absolute():
            raise BusinessRuleError("absolute operator paths required")
        independent_path(path, directory=key == "scratch")
    if config.get("runtime", "codex") == "codex":
        profile = Path(config["seccomp_profile"])
        if not profile.is_absolute() or not profile.is_file() or profile.is_symlink():
            raise BusinessRuleError("absolute independent seccomp profile required")
        independent_path(profile)
        if hashlib.sha256(profile.read_bytes()).hexdigest() != config["seccomp_sha256"]:
            raise BusinessRuleError("seccomp profile digest mismatch")
        if config.get("codex_auth_volume") != "techlead-review-poc_codex_auth":
            raise BusinessRuleError("dedicated POC Codex auth volume required")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/@:-]*", config["image"]):
        raise BusinessRuleError("invalid runtime image")
    if not isinstance(config.get("model"), str) or not config["model"].strip():
        raise BusinessRuleError("operator-selected model required")
    roots = set()
    for task in config["tasks"].values():
        root = Path(task["worktree"])
        if not root.is_absolute() or is_linked(root) or not root.is_dir():
            raise BusinessRuleError("dedicated canonical worktree required")
        if str(root.resolve()) != str(root) or root.resolve() in roots:
            raise BusinessRuleError("duplicate or noncanonical worktree")
        if not (root / ".git").is_file():
            raise BusinessRuleError("task requires linked Git worktree")
        independent_path(root, directory=True)
        roots.add(root.resolve())
        if not task.get("write_paths"):
            raise BusinessRuleError("explicit write path allowlist required")
        for prefix in task["write_paths"]:
            safe_path(prefix)
    if (
        type(config.get("timeout_seconds", 1800)) is not int
        or not 60 <= config.get("timeout_seconds", 1800) <= 3600
    ):
        raise BusinessRuleError("invalid timeout")


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
                raise BusinessRuleError("linked source directory")
            if name in GENERATED:
                dirs.remove(name)
        for name in sorted(files):
            check_deadline()
            path = Path(directory) / name
            info = path.lstat()
            if is_linked(path) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise BusinessRuleError("unsafe source file")
            file_count += 1
            total_bytes += info.st_size
            if (
                file_count > MAX_SNAPSHOT_FILES
                or info.st_size > MAX_SNAPSHOT_FILE_BYTES
                or total_bytes > MAX_SNAPSHOT_TOTAL_BYTES
            ):
                raise BusinessRuleError(
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
                        raise BusinessRuleError("source file changed while hashing")
                    digest.update(chunk)
                    remaining -= len(chunk)
                if handle.read(1):
                    raise BusinessRuleError("source file grew while hashing")
            if path.stat().st_size != info.st_size:
                raise BusinessRuleError("source file size changed while hashing")
    return digest.hexdigest()


class CoderRuntime:
    """Trusted Worker host with explicitly injected execution state."""

    def __init__(
        self,
        config: dict,
        handoff_provider: HandoffUseCaseProvider,
        *,
        dispatch: IDispatchEvidence,
        ci: ICiTracking,
        leadership_guard: Callable[[], bool],
        registry: RuntimeRegistry,
        sandbox: SandboxManager,
    ) -> None:
        if (
            not callable(handoff_provider)
            or not callable(getattr(dispatch, "is_pre_launch", None))
            or not callable(getattr(dispatch, "retire_pre_launch", None))
            or not callable(getattr(dispatch, "record_runtime_evidence", None))
            or not callable(getattr(ci, "execute", None))
            or not callable(leadership_guard)
            or type(registry) is not RuntimeRegistry
            or type(sandbox) is not SandboxManager
            or not isinstance(sandbox._fence_dir, Path)
            or not sandbox._fence_dir.is_absolute()
        ):
            raise TypeError(
                "Worker requires injected workflow, dispatch evidence, CI tracking, "
                "leadership, runtime registry and durable sandbox"
            )
        validate_sandbox_state_dir(sandbox._fence_dir)
        self.config, self.handoff_provider = config, handoff_provider
        self.registry = registry
        self.sandbox = sandbox
        self.agent_runtime = AgentExecutionService(
            self,
            dispatch,
            ci,
            leadership_guard,
            require_ci_tracking=True,
        )

    def _code_request(self, claim: dict) -> RuntimeRequest:
        return RuntimeRequest(
            spec=runtime_spec_from_config(self.config),
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
    ) -> WorkerResult:
        if approved_image_id is None:
            # Only the built-in Codex device-login profile is exempt from
            # strict sandbox image approval.
            output = self.docker(claim, source, "code", prompt, code_plan=plan)
            return self.registry.result(request, output)

        policy = SandboxPolicy(timeout_seconds=self._remaining_seconds(claim))
        self.agent_runtime.require_leadership()
        launch = self.sandbox.run(
            policy,
            dispatch_id=claim["dispatch_id"],
            workspace=source,
            image=self.config["runtime_image"],
            approved_image_id=approved_image_id,
            command=plan.command,
            env=plan.env,
            stdin=prompt,
        )
        return self.registry.result(
            request, launch.output, observed_image_id=launch.image_id
        )

    @staticmethod
    def _remaining_seconds(claim: dict) -> int:
        remaining = int(claim["deadline"] - time.time())
        if remaining <= 0:
            raise BusinessRuleError("dispatch deadline exceeded")
        remaining = int(operation_timeout(remaining))
        if remaining <= 0:
            raise BusinessRuleError("dispatch deadline exceeded")
        return remaining

    def reconcile(self, dispatch_id: str) -> None:
        self.agent_runtime.require_leadership()
        if self.agent_runtime.dispatch.is_pre_launch(dispatch_id):
            if self.sandbox.has_launch_records(dispatch_id):
                raise BusinessRuleError(
                    "pre-launch evidence conflicts with Docker records"
                )
            return
        host, daemon_id = self.sandbox.recovery_engine(dispatch_id)
        self.agent_runtime.require_leadership()
        with cleanup_budget(), _bind_host(host):
            _assert_daemon(daemon_id)
            self.stop(dispatch_id)
            self.sandbox.stop(dispatch_id)
            _assert_daemon(daemon_id)

    def stop(self, dispatch_id: str) -> None:
        if not re.fullmatch(r"[a-f0-9]{32}", dispatch_id):
            raise BusinessRuleError("invalid dispatch identifier")
        host, daemon_id = self.sandbox.recovery_engine(dispatch_id)
        with cleanup_budget(), _bind_host(host):
            _assert_daemon(daemon_id)
            for suffix in ("code", "test", "probe", "prepare", "publish"):
                name = f"my-wfc-{dispatch_id}-{suffix}"
                # docker rm -f is scoped to our exact persisted names. No v2 operations.
                found = sandbox.docker_command(
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
                    sandbox.docker_command(["docker", "rm", "-f", name])
                if sandbox.docker_command(
                    [
                        "docker",
                        "container",
                        "ls",
                        "-aq",
                        "--filter",
                        f"name=^/{name}$",
                    ]
                ).strip():
                    raise BusinessRuleError("runtime termination not confirmed")
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
            self.agent_runtime.require_leadership()
            existing = self.sandbox._engine_path(dispatch_id).exists()
            if existing and self.agent_runtime.dispatch.is_pre_launch(dispatch_id):
                raise BusinessRuleError(
                    "pre-launch evidence conflicts with Docker records"
                )
            host = (
                self.sandbox.recovery_engine(dispatch_id)[0]
                if existing
                else _selected_host()
            )
            with _bind_host(host):
                if not existing:
                    self.agent_runtime.require_leadership()
                    self._record_engine(dispatch_id, host, _require_engine())
                return self._docker_bound(claim, stage, mode, prompt, code_plan)
        return self._docker_bound(claim, stage, mode, prompt, code_plan)

    def _docker_bound(
        self,
        claim: dict,
        stage: Path,
        mode: str,
        prompt: str,
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
                raise BusinessRuleError("seccomp profile changed")
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
                raise BusinessRuleError(
                    "legacy Codex profile requires the Codex code plan"
                )
            volume = self.config.get("codex_auth_volume")
            if volume != "techlead-review-poc_codex_auth":
                raise BusinessRuleError("dedicated POC Codex auth volume required")
            sandbox.docker_command(["docker", "volume", "inspect", volume])
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
        self.agent_runtime.require_leadership()
        return sandbox.run_container(args, prompt, remaining)

    def execute_assignment(
        self, claim: DispatchClaim, assignment: DispatchAssignment
    ) -> str:
        """Map typed dispatch and assignment values at the trusted Worker boundary."""
        worker_claim = {
            "dispatch_id": claim.dispatch_id,
            "task_id": claim.task_id,
            "input_sha": claim.input_sha,
            "deadline": claim.deadline,
            "round_number": claim.round_number,
        }
        return self.execute(
            worker_claim, coder_assignment_response(assignment["details"])
        )

    def execute(self, claim: dict, context: dict) -> str:
        self.agent_runtime.require_leadership()
        request = self._code_request(claim)
        plan = self.registry.plan(request)
        approved_image_id = self.registry.approved_image_id(request)
        if approved_image_id is not None and not self.config.get("runtime_image"):
            raise BusinessRuleError(
                "explicit sandbox image required for runtime plugin"
            )
        host = _selected_host()
        with _bind_host(host):
            self.agent_runtime.require_leadership()
            daemon_id = _require_engine()
            self._record_engine(claim["dispatch_id"], host, daemon_id)
            result = self._execute_bound(
                claim, context, request, plan, approved_image_id
            )
            self.agent_runtime.require_leadership()
            _assert_daemon(daemon_id)
            self.agent_runtime.require_leadership()
            return result

    def _record_engine(self, dispatch_id: str, host: str, daemon_id: str) -> None:
        self.agent_runtime.require_leadership()
        self.agent_runtime.dispatch.retire_pre_launch(dispatch_id)
        self.agent_runtime.require_leadership()
        self.sandbox.record_engine(dispatch_id, host, daemon_id)

    def release_engine(self, dispatch_id: str) -> None:
        """Drop the pin only after the dispatch ledger reaches a terminal state."""
        self.agent_runtime.require_leadership()
        self.sandbox.clear_engine(dispatch_id)

    def _execute_bound(
        self,
        claim: dict,
        context: dict,
        request: RuntimeRequest,
        plan: RuntimePlan,
        approved_image_id: str | None,
    ) -> str:
        return self.agent_runtime.execute(
            claim, context, request, plan, approved_image_id
        )

    def _prepare_stage(self, claim: dict, context: dict) -> tuple[Path, dict]:
        task = self.config["tasks"][context["task_id"]]
        stage = Path(task["worktree"]) / ".runtime" / "dispatch" / claim["dispatch_id"]
        root = Path(task["worktree"]).resolve(strict=True)
        if not stage.resolve().is_relative_to(root):
            raise BusinessRuleError("dispatch path escapes allowed worktree")
        for parent in stage.parents:
            if parent == root:
                break
            if parent.exists() and is_linked(parent):
                raise BusinessRuleError("linked dispatch parent")
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
        return stage, envelope

    @staticmethod
    def _source(stage: Path) -> Path:
        source = stage / "source"
        if not source.is_dir() or is_linked(source):
            raise BusinessRuleError("worker source missing or linked")
        return source

    @staticmethod
    def _source_digest(source: Path) -> str:
        return source_digest(source)

    @staticmethod
    def _check_source_digest(source: Path, expected: str) -> None:
        if source_digest(source) != expected:
            raise BusinessRuleError("test phase mutated source")

    def _assert_engine(self, dispatch_id: str) -> None:
        _, daemon_id = self.sandbox.recovery_engine(dispatch_id)
        _assert_daemon(daemon_id)

    def _validated_result(self, stage: Path, claim: dict, context: dict) -> dict:
        result_path = stage / "result.json"
        if is_linked(result_path) or result_path.stat().st_size > 65536:
            raise BusinessRuleError("unsafe worker result")
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
            raise BusinessRuleError("incomplete worker result")
        if not isinstance(result["pr_url"], str) or not re.fullmatch(
            rf"https://github\.com/{re.escape(REPOSITORY)}/pull/[1-9][0-9]*",
            result["pr_url"],
        ):
            raise BusinessRuleError("worker PR reference differs from repository")
        validate_sha(result["head_sha"])
        for field in ("task_id", "repository", "branch", "base_sha"):
            if result[field] != context[field]:
                raise BusinessRuleError("worker result differs from authorized task")
        if result["input_sha"] != claim["input_sha"]:
            raise BusinessRuleError("worker result input mismatch")
        return result

    def _register_handoff(
        self,
        context: dict,
        result: dict,
        test_summary: str,
    ) -> None:
        task = self.config["tasks"][context["task_id"]]
        request = HandoffRequest(
            context["task_id"],
            context["repository"],
            context["branch"],
            context["base_sha"],
            result["head_sha"],
            task["handoff_path"],
            test_summary + "; worker PR: " + result["pr_url"],
        )
        with self.handoff_provider() as handoff:
            handoff.execute(request)
