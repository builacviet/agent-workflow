"""Layer: Infrastructure
Responsibility: Execute Docker stages and manage durable engine admission and recovery.
Dependencies: Application dispatch and runtime contracts, Domain rules and sandbox operations.
"""

import hashlib
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from agent_workflow.application.dispatch.ports import DispatchClaim
from agent_workflow.application.runtime.dtos import RuntimePlan
from agent_workflow.application.runtime.ports import IDispatchLaunchState
from agent_workflow.budget import check_deadline, cleanup_budget, operation_timeout
from agent_workflow.domain.exceptions import BusinessRuleError
from agent_workflow.infrastructure.runtime import sandbox
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
from agent_workflow.sandbox_state_paths import validate_sandbox_state_dir


class DockerRuntime:
    """Docker adapter for a Worker scope, with no phase orchestration."""

    def __init__(
        self,
        config: dict,
        *,
        dispatch: IDispatchLaunchState,
        leadership_guard: Callable[[], bool],
        sandbox: SandboxManager,
    ) -> None:
        if (
            not callable(getattr(dispatch, "is_pre_launch", None))
            or not callable(getattr(dispatch, "retire_pre_launch", None))
            or not callable(leadership_guard)
            or type(sandbox) is not SandboxManager
            or not isinstance(sandbox._fence_dir, Path)
            or not sandbox._fence_dir.is_absolute()
        ):
            raise TypeError(
                "Worker requires injected dispatch, leadership and durable sandbox"
            )
        validate_sandbox_state_dir(sandbox._fence_dir)
        self.config = config
        self.dispatch = dispatch
        self.sandbox = sandbox
        self._leadership_guard = leadership_guard

    def require_leadership(self) -> None:
        check_deadline()
        if self._leadership_guard() is not True:
            raise PermissionError("runtime leadership guard rejected execution")
        check_deadline()

    def validate_image(self, approved_image_id: str | None) -> None:
        if approved_image_id is not None and not self.config.get("runtime_image"):
            raise BusinessRuleError(
                "explicit sandbox image required for runtime plugin"
            )

    @contextmanager
    def bind_engine(self, dispatch_id: str) -> Iterator[None]:
        self.require_leadership()
        host = _selected_host()
        with _bind_host(host):
            self.require_leadership()
            daemon_id = _require_engine()
            self._record_engine(dispatch_id, host, daemon_id)
            yield
            self.require_leadership()
            _assert_daemon(daemon_id)
            self.require_leadership()

    def run_code(
        self,
        claim: DispatchClaim,
        source: Path,
        plan: RuntimePlan,
        prompt: str,
        approved_image_id: str | None,
    ) -> tuple[bytes, str | None]:
        if approved_image_id is None:
            # Only the built-in Codex device-login profile is exempt from
            # strict sandbox image approval.
            output = self.docker(claim, source, "code", prompt, code_plan=plan)
            return output, None

        policy = SandboxPolicy(timeout_seconds=self._remaining_seconds(claim))
        self.require_leadership()
        launch = self.sandbox.run(
            policy,
            dispatch_id=claim.dispatch_id,
            workspace=source,
            image=self.config["runtime_image"],
            approved_image_id=approved_image_id,
            command=plan.command,
            env=plan.env,
            stdin=prompt,
        )
        return launch.output, launch.image_id

    @staticmethod
    def _remaining_seconds(claim: DispatchClaim) -> int:
        remaining = int(claim.deadline - time.time())
        if remaining <= 0:
            raise BusinessRuleError("dispatch deadline exceeded")
        remaining = int(operation_timeout(remaining))
        if remaining <= 0:
            raise BusinessRuleError("dispatch deadline exceeded")
        return remaining

    def reconcile(self, dispatch_id: str) -> None:
        self.require_leadership()
        if self.dispatch.is_pre_launch(dispatch_id):
            if self.sandbox.has_launch_records(dispatch_id):
                raise BusinessRuleError(
                    "pre-launch evidence conflicts with Docker records"
                )
            return
        host, daemon_id = self.sandbox.recovery_engine(dispatch_id)
        self.require_leadership()
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
        claim: DispatchClaim,
        stage: Path,
        mode: str,
        prompt: str = "",
        *,
        code_plan: RuntimePlan | None = None,
    ) -> bytes:
        dispatch_id = claim.dispatch_id
        host = _DOCKER_HOST.get()
        if host is None:
            # The standalone probe/test entry point also needs a durable pin.
            self.require_leadership()
            existing = self.sandbox._engine_path(dispatch_id).exists()
            if existing and self.dispatch.is_pre_launch(dispatch_id):
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
                    self.require_leadership()
                    self._record_engine(dispatch_id, host, _require_engine())
                return self._docker_bound(claim, stage, mode, prompt, code_plan)
        return self._docker_bound(claim, stage, mode, prompt, code_plan)

    def _docker_bound(
        self,
        claim: DispatchClaim,
        stage: Path,
        mode: str,
        prompt: str,
        code_plan: RuntimePlan | None,
    ) -> bytes:
        host, daemon_id = self.sandbox.recovery_engine(claim.dispatch_id)
        _assert_selected_host(host)
        _assert_daemon(daemon_id)
        name = f"my-wfc-{claim.dispatch_id}-{mode}"
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
        self.require_leadership()
        return sandbox.run_container(args, prompt, remaining)

    def _record_engine(self, dispatch_id: str, host: str, daemon_id: str) -> None:
        self.require_leadership()
        self.dispatch.retire_pre_launch(dispatch_id)
        self.require_leadership()
        self.sandbox.record_engine(dispatch_id, host, daemon_id)

    def release_engine(self, dispatch_id: str) -> None:
        """Drop the pin only after the dispatch ledger reaches a terminal state."""
        self.require_leadership()
        self.sandbox.clear_engine(dispatch_id)

    def assert_engine(self, dispatch_id: str) -> None:
        _, daemon_id = self.sandbox.recovery_engine(dispatch_id)
        _assert_daemon(daemon_id)
