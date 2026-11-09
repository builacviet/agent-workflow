"""Credential-free Docker execution for trusted runtime plans."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from uuid import uuid4

from agent_workflow.budget import cleanup_budget, dispatch_budget, operation_timeout
from agent_workflow.integrations.github import WorkflowError

_DISPATCH_LABEL = "org.agent-workflow.sandbox.dispatch"
_CONTAINER_ID = re.compile(r"[0-9a-f]{64}")
_DISPATCH_ID = re.compile(r"[0-9a-f]{32}")
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
_DAEMON_ID = re.compile(r"[A-Za-z0-9:._-]{8,180}")
_IMAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/@:-]*")
_PHASE = re.compile(r"[a-z][a-z0-9-]{0,19}")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SAFE_IMAGE_ENV = frozenset({
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TZ", "TMPDIR",
    "PYTHON_VERSION", "PYTHON_SHA256", "PYTHON_PIP_VERSION", "PYTHON_SETUPTOOLS_VERSION",
    "PYTHON_GET_PIP_URL", "PYTHON_GET_PIP_SHA256", "PYTHONUNBUFFERED",
    "PYTHONDONTWRITEBYTECODE", "PYTHONPATH", "GPG_KEY",
})
_PROXY_NAMES = tuple(
    variant
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "FTP_PROXY")
    for variant in (name, name.lower())
)


@dataclass(frozen=True, slots=True)
class SandboxPolicy:
    """Docker policy v1. Only an offline, non-root sandbox is supported."""

    version: int = 1
    workspace_writable: bool = True
    network: str = "none"
    pids_limit: int = 256
    memory_mib: int = 2048
    cpus: int = 2
    timeout_seconds: int = 1800

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != 1:
            raise ValueError("unsupported sandbox policy version")
        if type(self.workspace_writable) is not bool:
            raise ValueError("workspace write policy must be boolean")
        if type(self.network) is not str or self.network != "none":
            raise ValueError("sandbox supports only isolated networking")
        for name, maximum in (
            ("pids_limit", 256),
            ("memory_mib", 2048),
            ("cpus", 2),
            ("timeout_seconds", 3600),
        ):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"invalid sandbox {name}")


def docker_security_args(policy: SandboxPolicy) -> list[str]:
    """Common Docker process and resource limits, independent of networking."""
    if type(policy) is not SandboxPolicy:
        raise TypeError("sandbox requires a validated SandboxPolicy")
    return [
        "--read-only",
        "--user",
        "10001:10001",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        f"--pids-limit={policy.pids_limit}",
        f"--memory={policy.memory_mib}m",
        f"--cpus={policy.cpus}",
        "--log-driver=none",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=512m,mode=1777",
    ]


def _docker(args: list[str]) -> bytes:
    # Reuse the trusted runner's subprocess and shared dispatch budget.
    from agent_workflow.runners.coder_runtime import docker_command

    return docker_command(["docker", *args])


def _require_engine() -> None:
    raw = _docker([
        "info",
        "--format",
        '{{json .ID}} {{json .OSType}} {{json .MemoryLimit}} {{json .CPUCfsQuota}} '
        '{{json .PidsLimit}} {{json .SwapLimit}} {{json .SecurityOptions}}',
    ]).decode("utf-8", errors="replace").split(maxsplit=6)
    try:
        daemon, os_type, memory, cpu, pids, swap, security = (json.loads(item) for item in raw)
    except (TypeError, ValueError):
        raise WorkflowError("invalid Docker capability response") from None
    if (
        not isinstance(daemon, str)
        or not _DAEMON_ID.fullmatch(daemon)
        or os_type != "linux"
        or not all(enabled is True for enabled in (memory, cpu, pids, swap))
        or not isinstance(security, list)
        or "name=seccomp,profile=builtin" not in security
    ):
        raise WorkflowError("Docker cannot enforce sandbox resource and security policy")
    return daemon


def _local_image(image: str) -> str:
    try:
        result = json.loads(_docker(["image", "inspect", image]))
        metadata, = result
        identifier = metadata["Id"]
        environment = metadata["Config"].get("Env") or []
        if (
            metadata["Os"] == "linux"
            and _IMAGE_ID.fullmatch(identifier)
            and not metadata["Config"].get("Volumes")
            and isinstance(environment, list)
            and all(
                isinstance(item, str)
                and "=" in item
                and item.split("=", 1)[0] in _SAFE_IMAGE_ENV
                and len(item) < 4096
                for item in environment
            )
        ):
            return identifier
    except (AttributeError, KeyError, TypeError, ValueError):
        pass
    raise WorkflowError("sandbox requires a local Linux image without declared volumes")


def _workspace_bind(workspace: Path, writable: bool) -> str:
    # The trusted caller supplies an exclusive, disposable snapshot.
    from agent_workflow.runners.coder_runtime import is_linked

    try:
        if (
            not workspace.is_absolute()
            or workspace.resolve(strict=True) != workspace
            or is_linked(workspace)
            or not workspace.is_dir()
            or any(character in str(workspace) for character in ',\"\r\n')
            or workspace.parent == workspace
        ):
            raise WorkflowError("sandbox requires a canonical workspace directory")
        for root, dirs, files in os.walk(workspace, onerror=_walk_error):
            operation_timeout(float("inf"))
            for name in dirs + files:
                item = Path(root) / name
                info = item.lstat()
                if is_linked(item) or (
                    not stat.S_ISDIR(info.st_mode)
                    and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1)
                ):
                    raise WorkflowError("sandbox workspace contains a link or special file")
    except OSError:
        raise WorkflowError("sandbox workspace unavailable") from None
    mount = f"type=bind,src={workspace},dst=/workspace,bind-recursive=disabled"
    return mount if writable else mount + ",readonly"


def _walk_error(error: OSError) -> None:
    raise error


def _environment(env: tuple[tuple[str, str], ...]) -> list[str]:
    # Explicit empty values prevent Docker's local client proxy defaults from
    # injecting proxy credentials. Docker never receives a bare variable name.
    args = [arg for name in _PROXY_NAMES for arg in ("--env", name + "=")]
    if not isinstance(env, tuple):
        raise ValueError("sandbox environment must be explicit name/value pairs")
    names = set(_PROXY_NAMES)
    for pair in env:
        if not isinstance(pair, tuple) or len(pair) != 2:
            raise ValueError("sandbox environment must be explicit name/value pairs")
        key, value = pair
        if (
            not isinstance(key, str)
            or not _ENV_NAME.fullmatch(key)
            or key in names
            or not isinstance(value, str)
            or not value.isprintable()
            or len(value) > 4096
        ):
            raise ValueError("invalid sandbox environment")
        names.add(key)
        args.extend(("--env", f"{key}={value}"))
    return args


def _dispatch_prefix(dispatch_id: str) -> str:
    if not isinstance(dispatch_id, str) or not _DISPATCH_ID.fullmatch(dispatch_id):
        raise ValueError("invalid sandbox dispatch identifier")
    return f"my-wfc-sandbox-{dispatch_id}"


@dataclass(frozen=True, slots=True)
class SandboxRunResult:
    output: bytes
    image_id: str


def _sync_directory(path: Path) -> None:
    # Windows does not support opening directories with os.open.
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class SandboxManager:
    """Execute an offline workload and remove only its owned Docker resources."""

    def __init__(self, fence_dir: Path | None = None) -> None:
        self._fence_dir = fence_dir

    def run(
        self,
        policy: SandboxPolicy,
        *,
        dispatch_id: str,
        workspace: Path,
        image: str,
        approved_image_id: str,
        command: tuple[str, ...],
        stdin: str = "",
        phase: str = "code",
        env: tuple[tuple[str, str], ...] = (),
    ) -> SandboxRunResult:
        """Launch the pinned image and return bounded output with observed identity."""
        from agent_workflow.runners.coder_runtime import run_container

        security = docker_security_args(policy)
        prefix = _dispatch_prefix(dispatch_id)
        if not isinstance(phase, str) or not _PHASE.fullmatch(phase):
            raise ValueError("invalid sandbox phase")
        if not isinstance(image, str) or not _IMAGE.fullmatch(image):
            raise ValueError("invalid sandbox image")
        if not isinstance(approved_image_id, str) or not _IMAGE_ID.fullmatch(
            approved_image_id
        ):
            raise WorkflowError("sandbox requires an approved immutable image ID")
        fence = self._fence_path(dispatch_id)
        if fence is None:
            raise WorkflowError("sandbox requires a durable create fence")
        if (
            not isinstance(command, tuple)
            or not command
            or any(not isinstance(arg, str) or "\0" in arg for arg in command)
            or not command[0]
            or not isinstance(stdin, str)
        ):
            raise ValueError("invalid sandbox command")
        environment = _environment(env)
        name = f"{prefix}-{phase}-{uuid4().hex}"
        deadline = time.time() + operation_timeout(policy.timeout_seconds)
        with dispatch_budget(deadline):
            mount = _workspace_bind(workspace, policy.workspace_writable)
            daemon_id = _require_engine()
            if _local_image(image) != approved_image_id:
                raise WorkflowError("sandbox image reference differs from approved pin")
            args = [
                "create",
                "--name",
                name,
                "--label",
                f"{_DISPATCH_LABEL}={dispatch_id}",
                "--pull=never",
                "--network=none",
                "--ipc=none",
                "--init",
                f"--memory-swap={policy.memory_mib}m",
                "--workdir=/workspace",
                "--mount",
                mount,
                "-i",
                *security,
                *environment,
                approved_image_id,
                *command,
            ]
            scope = self._scope_path(dispatch_id)
            self._record_scope(scope, dispatch_id, name, daemon_id, deadline)
            try:
                self._record_create(fence, name)
                self._update_scope(scope, "SUBMITTED")
                identifier = _docker(args).decode("ascii", errors="replace").strip()
                if not _CONTAINER_ID.fullmatch(identifier):
                    raise WorkflowError("Docker did not identify the sandbox container")
                observed_image_id = self._inspect_image(identifier, name, dispatch_id)
                self._update_scope(scope, "CREATED")
                self._clear_create(fence)
                if observed_image_id != approved_image_id:
                    raise WorkflowError("sandbox container image differs from approved pin")
                # Independent trusted host watchdog must acknowledge readiness
                # before the untrusted workload is allowed to start.
                self._start_watchdog(scope, dispatch_id)
                remaining = math.ceil(operation_timeout(policy.timeout_seconds))
                output = run_container(
                    ["docker", "start", "-ai", identifier], stdin, remaining
                )
                return SandboxRunResult(output, observed_image_id)
            finally:
                self._assert_scope(scope, dispatch_id)
                self._settle_create(
                    dispatch_id, fence, self._name_pattern(prefix), scope
                )
                self._cleanup(dispatch_id, re.escape(name))
                self._clear_scope(scope)

    def stop(self, dispatch_id: str) -> None:
        """Only the original Docker engine may settle an owned execution."""
        prefix = _dispatch_prefix(dispatch_id)
        pattern = self._name_pattern(prefix)
        fence = self._fence_path(dispatch_id)
        scope = self._scope_path(dispatch_id)
        if scope is not None and scope.exists():
            self._assert_scope(scope, dispatch_id)
        elif fence is not None and fence.exists():
            raise WorkflowError("sandbox create lacks daemon identity; dispatch stays blocked")
        self._settle_create(dispatch_id, fence, pattern, scope)
        self._cleanup(dispatch_id, pattern)
        if scope is not None and scope.exists():
            self._clear_scope(scope)

    @staticmethod
    def _name_pattern(prefix: str) -> str:
        return rf"{re.escape(prefix)}-[a-z][a-z0-9-]{{0,19}}-[0-9a-f]{{32}}"

    def _fence_path(self, dispatch_id: str) -> Path | None:
        if self._fence_dir is None:
            return None
        return self._fence_dir / dispatch_id

    def _scope_path(self, dispatch_id: str) -> Path | None:
        fence = self._fence_path(dispatch_id)
        return fence.with_name(fence.name + ".scope") if fence else None

    @staticmethod
    def _scope_data(scope: Path) -> dict:
        try:
            record = json.loads(scope.read_text(encoding="ascii"))
        except (OSError, ValueError):
            raise WorkflowError("missing or invalid durable sandbox scope") from None
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("dispatch_id"), str)
            or not _DISPATCH_ID.fullmatch(record["dispatch_id"])
            or not isinstance(record.get("daemon_id"), str)
            or not _DAEMON_ID.fullmatch(record["daemon_id"])
            or record.get("state") not in {"PREPARED", "SUBMITTED", "CREATED"}
            or not isinstance(record.get("name"), str)
            or not isinstance(record.get("deadline"), (int, float))
            or not isinstance(record.get("monotonic_deadline"), (int, float))
        ):
            raise WorkflowError("invalid durable sandbox scope")
        return record

    @staticmethod
    def _record_scope(
        scope: Path, dispatch_id: str, name: str, daemon_id: str, deadline: float
    ) -> None:
        if not isinstance(daemon_id, str) or not _DAEMON_ID.fullmatch(daemon_id):
            raise WorkflowError("Docker daemon has no trustworthy identity")
        scope.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = json.dumps({
            "dispatch_id": dispatch_id, "name": name,
            "daemon_id": daemon_id, "deadline": deadline,
            "monotonic_deadline": time.monotonic() + max(0., deadline - time.time()),
            "state": "PREPARED",
        }, sort_keys=True)
        try:
            with scope.open("x", encoding="ascii") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            raise WorkflowError("existing sandbox execution requires reconciliation") from None
        _sync_directory(scope.parent)

    @classmethod
    def _update_scope(cls, scope: Path, state: str) -> None:
        record = cls._scope_data(scope)
        record["state"] = state
        temporary = scope.with_name(scope.name + "." + uuid4().hex + ".tmp")
        try:
            with temporary.open("x", encoding="ascii") as handle:
                json.dump(record, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, scope)
            _sync_directory(scope.parent)
        finally:
            temporary.unlink(missing_ok=True)

    @classmethod
    def _assert_scope(cls, scope: Path, dispatch_id: str) -> None:
        record = cls._scope_data(scope)
        if record["dispatch_id"] != dispatch_id or _require_engine() != record["daemon_id"]:
            raise WorkflowError("Docker daemon identity changed; dispatch remains blocked")

    @staticmethod
    def _clear_scope(scope: Path) -> None:
        scope.unlink()
        scope.with_name(scope.name + ".ready").unlink(missing_ok=True)
        _sync_directory(scope.parent)

    @staticmethod
    def _start_watchdog(scope: Path, dispatch_id: str) -> None:
        """Launch a detached, authenticated-to-engine timeout process.

        The watchdog is a trusted host process; it never enters the sandbox.
        The controller does not launch the workload unless readiness is proven.
        """
        ready = scope.with_name(scope.name + ".ready")
        environment = os.environ.copy()
        if not environment.get("DOCKER_CONTEXT") and not environment.get("DOCKER_HOST"):
            try:
                context = subprocess.run(
                    ["docker", "context", "show"], check=True, capture_output=True,
                    timeout=operation_timeout(5), text=True,
                ).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                raise WorkflowError("cannot pin Docker context for watchdog") from None
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", context):
                raise WorkflowError("invalid Docker context for watchdog")
            environment["DOCKER_CONTEXT"] = context
        command = [
            sys.executable, "-m", "agent_workflow.runners.sandbox_watchdog",
            str(scope), dispatch_id,
        ]
        options = {
            "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL, "close_fds": True,
            "env": environment,
        }
        if os.name == "nt":
            options["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            )
        else:
            options["start_new_session"] = True
        try:
            watchdog = subprocess.Popen(command, **options)
        except OSError:
            raise WorkflowError("cannot launch independent sandbox watchdog") from None
        start = time.monotonic()
        while time.monotonic() - start < operation_timeout(3):
            if ready.exists():
                return
            if watchdog.poll() is not None:
                raise WorkflowError("independent sandbox watchdog terminated")
            time.sleep(0.05)
        raise WorkflowError("independent sandbox watchdog did not acknowledge readiness")

    @staticmethod
    def _record_create(fence: Path, name: str) -> None:
        fence.parent.mkdir(mode=0o700, exist_ok=True)
        try:
            with fence.open("x", encoding="ascii") as record:
                record.write(name)
                record.flush()
                os.fsync(record.fileno())
        except FileExistsError:
            raise WorkflowError("a sandbox create is already pending") from None
        _sync_directory(fence.parent)
        _sync_directory(fence.parent.parent)

    @staticmethod
    def _clear_create(fence: Path) -> None:
        fence.unlink()
        _sync_directory(fence.parent)

    def _settle_create(
        self, dispatch_id: str, fence: Path | None,
        name_pattern: str, scope: Path | None = None,
    ) -> None:
        if fence is None:
            return
        try:
            name = fence.read_text(encoding="ascii")
        except FileNotFoundError:
            return
        if not re.fullmatch(name_pattern, name):
            raise WorkflowError("invalid sandbox create fence")
        with cleanup_budget():
            owned = self._owned(dispatch_id, re.escape(name))
            if len(owned) != 1:
                if (
                    not owned and scope is not None
                    and self._scope_data(scope)["state"] == "PREPARED"
                ):
                    self._clear_create(fence)
                    return
                raise WorkflowError(
                    "sandbox Docker create remains uncertain; dispatch stays blocked"
                )
        # The exact owned name is affirmative evidence this create settled.
        # Clear intent before removal so a crash cannot lose that evidence.
        self._clear_create(fence)

    @staticmethod
    def _inspect_image(identifier: str, name: str, dispatch_id: str) -> str:
        raw = _docker([
            "container", "inspect", "--format",
            '{{json .Id}} {{json .Name}} {{json .Image}} '
            '{{json (index .Config.Labels "' + _DISPATCH_LABEL + '")}}',
            identifier,
        ])
        try:
            container_id, container_name, image_id, label = (
                json.loads(value) for value in raw.split(maxsplit=3)
            )
            if (
                container_id == identifier
                and container_name == "/" + name
                and label == dispatch_id
                and _IMAGE_ID.fullmatch(image_id)
            ):
                return image_id
        except (TypeError, ValueError):
            pass
        raise WorkflowError("could not verify sandbox container identity")

    def _cleanup(self, dispatch_id: str, name_pattern: str) -> None:
        with cleanup_budget():
            identifiers = self._owned(dispatch_id, name_pattern)
            for identifier in identifiers:
                _docker(["rm", "-f", identifier])
            if self._owned(dispatch_id, name_pattern):
                raise WorkflowError("sandbox termination not confirmed")

    def _owned(self, dispatch_id: str, name_pattern: str) -> list[str]:
        raw = _docker([
            "container",
            "ls",
            "-a",
            "--no-trunc",
            "--filter",
            f"name=^/{name_pattern}$",
            "--filter",
            f"label={_DISPATCH_LABEL}={dispatch_id}",
            "--format",
            "{{json .}}",
        ])
        identifiers = []
        try:
            for line in raw.splitlines():
                row = json.loads(line)
                if (
                    not re.fullmatch(name_pattern, row["Names"])
                    or not _CONTAINER_ID.fullmatch(row["ID"])
                    or f"{_DISPATCH_LABEL}={dispatch_id}" not in row["Labels"].split(",")
                ):
                    raise ValueError("container ownership mismatch")
                identifiers.append(row["ID"])
        except (AttributeError, KeyError, TypeError, ValueError):
            raise WorkflowError("could not verify sandbox ownership") from None
        return identifiers
