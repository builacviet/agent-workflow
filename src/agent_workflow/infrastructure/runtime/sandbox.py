"""Layer: Infrastructure
Responsibility: Execute and reconcile trusted, isolated Docker runtime plans.
Dependencies: Application errors, runtime contracts and dispatch budgets.
"""

from __future__ import annotations

import json
import math
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from agent_workflow.application.exceptions import (
    RuntimeAdmissionError,
    RuntimeContractError,
    RuntimeExecutionError,
)
from agent_workflow.application.runtime.dtos import (
    credential_env_name,
    nonsecret_env_value,
)
from agent_workflow.budget import (
    check_deadline,
    cleanup_budget,
    dispatch_budget,
    operation_timeout,
)
from agent_workflow.domain.exceptions import BusinessRuleError
from agent_workflow.infrastructure.runtime.workspace import is_linked

_DISPATCH_LABEL = "org.agent-workflow.sandbox.dispatch"
_CONTAINER_ID = re.compile(r"[0-9a-f]{64}")
_DISPATCH_ID = re.compile(r"[0-9a-f]{32}")
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
_DAEMON_ID = re.compile(r"[A-Za-z0-9:._-]{8,180}")
_IMAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/@:-]*")
_PHASE = re.compile(r"[a-z][a-z0-9-]{0,19}")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SAFE_IMAGE_ENV = frozenset(
    {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TERM",
        "TZ",
        "TMPDIR",
        "PYTHON_VERSION",
        "PYTHON_SHA256",
        "PYTHON_PIP_VERSION",
        "PYTHON_SETUPTOOLS_VERSION",
        "PYTHON_GET_PIP_URL",
        "PYTHON_GET_PIP_SHA256",
        "PYTHONUNBUFFERED",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONPATH",
        "GPG_KEY",
    }
)
_PROXY_NAMES = tuple(
    variant
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "FTP_PROXY")
    for variant in (name, name.lower())
)


# A host bind is meaningful only on a local daemon. Resolve context names once
# to a concrete socket or named pipe; never use a mutable context as the pin.
_LOCAL_HOST = re.compile(r"(?:unix:///[^\s\x00]+|npipe:////\./pipe/[A-Za-z0-9_.-]+)")
_DOCKER_HOST: ContextVar[str | None] = ContextVar("sandbox_docker_host", default=None)


class DockerCreateRejected(RuntimeExecutionError):
    """Affirmative evidence that Docker did not accept the create request."""


def _valid_host(host: object) -> bool:
    return isinstance(host, str) and _LOCAL_HOST.fullmatch(host) is not None


def _selected_host() -> str:
    if not os.environ.get("DOCKER_CONTEXT") and os.environ.get("DOCKER_HOST"):
        host = os.environ["DOCKER_HOST"]
    else:
        try:
            host = subprocess.run(
                [
                    "docker",
                    "context",
                    "inspect",
                    "--format",
                    "{{.Endpoints.docker.Host}}",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=operation_timeout(5),
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            raise RuntimeExecutionError(
                "cannot resolve the local Docker endpoint"
            ) from None
    if not _valid_host(host):
        raise RuntimeAdmissionError(
            "sandbox requires a local Docker socket or named pipe"
        )
    return host


@contextmanager
def _bind_host(host: str) -> Iterator[None]:
    bound = _DOCKER_HOST.get()
    if not _valid_host(host) or (bound is not None and bound != host):
        raise RuntimeContractError("Docker endpoint changed; dispatch remains blocked")
    token = _DOCKER_HOST.set(host)
    try:
        yield
    finally:
        _DOCKER_HOST.reset(token)


def _docker_invocation(args: list[str]) -> tuple[list[str], dict[str, str] | None]:
    host = _DOCKER_HOST.get()
    if host is None:
        return args, None
    # DOCKER_CONTEXT can otherwise take precedence over endpoint selection.
    excluded = {
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_TLS",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
        "DOCKER_CUSTOM_HEADERS",
        *(name.upper() for name in _PROXY_NAMES),
    }
    env = {
        key: value for key, value in os.environ.items() if key.upper() not in excluded
    }
    return [args[0], "--host", host, *args[1:]], env


def _assert_daemon(daemon_id: str) -> None:
    if _require_engine() != daemon_id:
        raise RuntimeContractError(
            "Docker daemon identity changed; dispatch remains blocked"
        )


def _assert_selected_host(host: str) -> None:
    if _selected_host() != host:
        raise RuntimeContractError("Docker endpoint changed during execution")


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


def docker_command(args: list[str]) -> bytes:
    if not args or args[0] != "docker":
        raise RuntimeAdmissionError("Controller may only launch Docker")
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
        if args[1 : 1 + len(prefix)] == prefix:
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
        raise RuntimeExecutionError(f"Docker {operation} timed out") from None
    except subprocess.CalledProcessError as error:
        if operation == "create" and error.returncode == 1:
            # Only an exact daemon rejection of the requested immutable image
            # is terminal. Timeouts, transport errors and conflicts are not.
            rejection = (error.stderr or b"").strip()
            image = next(
                (arg for arg in args if re.fullmatch(r"sha256:[0-9a-f]{64}", arg)), None
            )
            if (
                image
                and rejection
                == b"Error response from daemon: No such image: "
                + image.encode("ascii")
            ):
                raise DockerCreateRejected(
                    "Docker rejected the sandbox image"
                ) from None
        raise RuntimeExecutionError(
            f"Docker {operation} failed (exit {error.returncode})"
        ) from None
    except (FileNotFoundError, PermissionError):
        if operation == "create":
            raise DockerCreateRejected("could not launch Docker create") from None
        raise RuntimeExecutionError(f"could not launch Docker {operation}") from None
    except OSError:
        raise RuntimeExecutionError(f"Docker {operation} I/O failed") from None


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
                        raise RuntimeExecutionError(
                            "runtime deadline or output budget exceeded"
                        )
            check_deadline()
            size = output.tell()
            if process.returncode or size > 4_000_000:
                raise RuntimeExecutionError(
                    "coder/test container failed or output exceeded limit"
                )
            output.seek(0)
            return output.read()
        except BaseException:
            process.kill()
            process.wait(timeout=10)
            raise


def _docker(args: list[str]) -> bytes:
    # Reuse the Docker transport and shared dispatch budget.
    return docker_command(["docker", *args])


def _require_engine() -> str:
    raw = (
        _docker(
            [
                "info",
                "--format",
                "{{json .ID}} {{json .OSType}} {{json .MemoryLimit}} {{json .CPUCfsQuota}} "
                "{{json .PidsLimit}} {{json .SwapLimit}} {{json .SecurityOptions}}",
            ]
        )
        .decode("utf-8", errors="replace")
        .split(maxsplit=6)
    )
    try:
        daemon, os_type, memory, cpu, pids, swap, security = (
            json.loads(item) for item in raw
        )
    except (TypeError, ValueError):
        raise RuntimeContractError("invalid Docker capability response") from None
    if (
        not isinstance(daemon, str)
        or not _DAEMON_ID.fullmatch(daemon)
        or os_type != "linux"
        or not all(enabled is True for enabled in (memory, cpu, pids, swap))
        or not isinstance(security, list)
        or "name=seccomp,profile=builtin" not in security
    ):
        raise RuntimeAdmissionError(
            "Docker cannot enforce sandbox resource and security policy"
        )
    return daemon


def _local_image(image: str) -> str:
    try:
        result = json.loads(_docker(["image", "inspect", image]))
        (metadata,) = result
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
                and nonsecret_env_value(item.split("=", 1)[1])
                for item in environment
            )
        ):
            return identifier
    except (AttributeError, KeyError, TypeError, ValueError):
        pass
    raise BusinessRuleError(
        "sandbox requires a local Linux image without declared volumes"
    )


def _workspace_bind(workspace: Path, writable: bool) -> str:
    # The trusted caller supplies an exclusive, disposable snapshot.
    try:
        if (
            not workspace.is_absolute()
            or workspace.resolve(strict=True) != workspace
            or is_linked(workspace)
            or not workspace.is_dir()
            or any(character in str(workspace) for character in ',"\r\n')
            or workspace.parent == workspace
        ):
            raise BusinessRuleError("sandbox requires a canonical workspace directory")
        for root, dirs, files in os.walk(workspace, onerror=_walk_error):
            operation_timeout(float("inf"))
            for name in dirs + files:
                item = Path(root) / name
                info = item.lstat()
                if is_linked(item) or (
                    not stat.S_ISDIR(info.st_mode)
                    and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1)
                ):
                    raise BusinessRuleError(
                        "sandbox workspace contains a link or special file"
                    )
    except OSError:
        raise BusinessRuleError("sandbox workspace unavailable") from None
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
    names = {name.upper() for name in _PROXY_NAMES}
    for pair in env:
        if not isinstance(pair, tuple) or len(pair) != 2:
            raise ValueError("sandbox environment must be explicit name/value pairs")
        key, value = pair
        if (
            not isinstance(key, str)
            or not _ENV_NAME.fullmatch(key)
            or key.upper() in names
            or credential_env_name(key)
            or not nonsecret_env_value(value)
        ):
            raise ValueError("invalid sandbox environment")
        names.add(key.upper())
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
        security = docker_security_args(policy)
        prefix = _dispatch_prefix(dispatch_id)
        if not isinstance(phase, str) or not _PHASE.fullmatch(phase):
            raise ValueError("invalid sandbox phase")
        if not isinstance(image, str) or not _IMAGE.fullmatch(image):
            raise ValueError("invalid sandbox image")
        if not isinstance(approved_image_id, str) or not _IMAGE_ID.fullmatch(
            approved_image_id
        ):
            raise BusinessRuleError("sandbox requires an approved immutable image ID")
        fence = self._fence_path(dispatch_id)
        if fence is None:
            raise BusinessRuleError("sandbox requires a durable create fence")
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
        host = _DOCKER_HOST.get() or _selected_host()
        with dispatch_budget(deadline), _bind_host(host):
            mount = _workspace_bind(workspace, policy.workspace_writable)
            daemon_id = _require_engine()
            if _local_image(image) != approved_image_id:
                raise BusinessRuleError(
                    "sandbox image reference differs from approved pin"
                )
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
            _assert_selected_host(host)
            self._record_scope(scope, dispatch_id, name, daemon_id, deadline, host)
            try:
                self._record_create(fence, name)
                _assert_daemon(daemon_id)
                self._update_scope(scope, "SUBMITTED")
                try:
                    identifier = _docker(args).decode("ascii", errors="replace").strip()
                except DockerCreateRejected:
                    self._update_scope(scope, "REJECTED")
                    raise
                if not _CONTAINER_ID.fullmatch(identifier):
                    raise BusinessRuleError(
                        "Docker did not identify the sandbox container"
                    )
                observed_image_id = self._inspect_image(identifier, name, dispatch_id)
                self._update_scope(scope, "CREATED")
                self._clear_create(fence)
                if observed_image_id != approved_image_id:
                    raise BusinessRuleError(
                        "sandbox container image differs from approved pin"
                    )
                # Independent trusted host watchdog must acknowledge readiness
                # before the untrusted workload is allowed to start.
                self._start_watchdog(scope, dispatch_id)
                _assert_selected_host(host)
                self._assert_scope(scope, dispatch_id)
                remaining = math.ceil(operation_timeout(policy.timeout_seconds))
                output = run_container(
                    ["docker", "start", "-ai", identifier], stdin, remaining
                )
                return SandboxRunResult(output, observed_image_id)
            finally:
                # Reconciliation must have its own budget: the execution
                # deadline may already be exhausted (including on Windows).
                # Preserve the original timeout unless real cleanup fails.
                with cleanup_budget():
                    self._assert_scope(scope, dispatch_id)
                    self._settle_create(
                        dispatch_id, fence, self._name_pattern(prefix), scope
                    )
                    self._cleanup(dispatch_id, re.escape(name))
                    self._assert_scope(scope, dispatch_id)
                    self._clear_scope(scope)

    def stop(self, dispatch_id: str) -> None:
        """Only the original Docker engine may settle an owned execution."""
        prefix = _dispatch_prefix(dispatch_id)
        pattern = self._name_pattern(prefix)
        fence = self._fence_path(dispatch_id)
        scope = self._scope_path(dispatch_id)
        if scope is None or not scope.exists():
            if fence is not None and fence.exists():
                raise BusinessRuleError(
                    "sandbox create lacks daemon identity; dispatch stays blocked"
                )
            if _DOCKER_HOST.get() is not None:
                with cleanup_budget():
                    self._cleanup(dispatch_id, pattern)
            return
        record = self._scope_data(scope)
        with cleanup_budget(), _bind_host(record["host"]):
            self._assert_scope(scope, dispatch_id)
            self._settle_create(dispatch_id, fence, pattern, scope)
            self._cleanup(dispatch_id, pattern)
            _assert_daemon(record["daemon_id"])
            self._clear_scope(scope)

    def _engine_path(self, dispatch_id: str) -> Path:
        _dispatch_prefix(dispatch_id)
        fence = self._fence_path(dispatch_id)
        if fence is None:
            raise BusinessRuleError("runtime requires durable Docker endpoint storage")
        return fence.with_name(fence.name + ".engine")

    def record_engine(self, dispatch_id: str, host: str, daemon_id: str) -> None:
        path = self._engine_path(dispatch_id)
        self._write_record(path, {"host": host, "daemon_id": daemon_id})

    def recovery_engine(self, dispatch_id: str) -> tuple[str, str]:
        _dispatch_prefix(dispatch_id)
        path = self._engine_path(dispatch_id)
        if not path.exists():
            scope = self._scope_path(dispatch_id)
            if scope is None or not scope.exists():
                raise BusinessRuleError(
                    "dispatch lacks its original Docker endpoint; stays blocked"
                )
            record = self._scope_data(scope)
        else:
            record = self._read_record(path)
        host, daemon_id = record.get("host"), record.get("daemon_id")
        if (
            not _valid_host(host)
            or not isinstance(daemon_id, str)
            or not _DAEMON_ID.fullmatch(daemon_id)
        ):
            raise BusinessRuleError("invalid durable Docker endpoint")
        return host, daemon_id

    def has_launch_records(self, dispatch_id: str) -> bool:
        engine = self._engine_path(dispatch_id)
        for path in (engine, engine.with_suffix(".scope"), engine.with_suffix("")):
            try:
                path.lstat()
            except FileNotFoundError:
                continue
            return True
        return False

    def clear_engine(self, dispatch_id: str) -> None:
        path = self._engine_path(dispatch_id)
        try:
            path.unlink()
        except FileNotFoundError:
            return
        _sync_directory(path.parent)

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
    def _read_record(path: Path) -> dict:
        try:
            record = json.loads(path.read_text(encoding="ascii"))
        except (OSError, ValueError):
            raise BusinessRuleError(
                "missing or invalid durable sandbox scope"
            ) from None
        if not isinstance(record, dict):
            raise BusinessRuleError("invalid durable sandbox scope")
        return record

    @classmethod
    def _scope_data(cls, scope: Path) -> dict:
        record = cls._read_record(scope)
        if (
            not isinstance(record.get("dispatch_id"), str)
            or not _valid_host(record.get("host"))
            or not _DISPATCH_ID.fullmatch(record["dispatch_id"])
            or not isinstance(record.get("daemon_id"), str)
            or not _DAEMON_ID.fullmatch(record["daemon_id"])
            or record.get("state")
            not in {"PREPARED", "SUBMITTED", "CREATED", "REJECTED"}
            or not isinstance(record.get("name"), str)
            or not isinstance(record.get("deadline"), (int, float))
            or not isinstance(record.get("monotonic_deadline"), (int, float))
        ):
            raise BusinessRuleError("invalid durable sandbox scope")
        return record

    @staticmethod
    def _write_record(path: Path, record: dict) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="ascii") as handle:
                json.dump(record, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            raise BusinessRuleError(
                "existing sandbox execution requires reconciliation"
            ) from None
        _sync_directory(path.parent)
        _sync_directory(path.parent.parent)

    @classmethod
    def _record_scope(
        cls,
        scope: Path,
        dispatch_id: str,
        name: str,
        daemon_id: str,
        deadline: float,
        host: str,
    ) -> None:
        if not isinstance(daemon_id, str) or not _DAEMON_ID.fullmatch(daemon_id):
            raise BusinessRuleError("Docker daemon has no trustworthy identity")
        cls._write_record(
            scope,
            {
                "dispatch_id": dispatch_id,
                "name": name,
                "host": host,
                "daemon_id": daemon_id,
                "deadline": deadline,
                "monotonic_deadline": time.monotonic()
                + max(0.0, deadline - time.time()),
                "state": "PREPARED",
            },
        )

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
        if record["dispatch_id"] != dispatch_id or record["host"] != _DOCKER_HOST.get():
            raise BusinessRuleError(
                "Docker endpoint differs from durable sandbox scope"
            )
        _assert_daemon(record["daemon_id"])

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
        command = [
            sys.executable,
            "-m",
            "agent_workflow.infrastructure.runtime.sandbox_watchdog",
            str(scope),
            dispatch_id,
        ]
        # Keep only host process bootstrap and local Docker configuration.
        allowed_env = {
            "PATH",
            "PYTHONPATH",
            "PYTHONHOME",
            "HOME",
            "USERPROFILE",
            "SYSTEMROOT",
            "WINDIR",
            "APPDATA",
            "LOCALAPPDATA",
            "TEMP",
            "TMP",
            "TMPDIR",
            "DOCKER_CONFIG",
        }
        options = {
            "env": {
                key: value
                for key, value in os.environ.items()
                if key.upper() in allowed_env
            },
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
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
            raise BusinessRuleError(
                "cannot launch independent sandbox watchdog"
            ) from None
        start = time.monotonic()
        while time.monotonic() - start < operation_timeout(3):
            if ready.exists():
                return
            if watchdog.poll() is not None:
                raise BusinessRuleError("independent sandbox watchdog terminated")
            time.sleep(0.05)
        raise BusinessRuleError(
            "independent sandbox watchdog did not acknowledge readiness"
        )

    @staticmethod
    def _record_create(fence: Path, name: str) -> None:
        fence.parent.mkdir(mode=0o700, exist_ok=True)
        try:
            with fence.open("x", encoding="ascii") as record:
                record.write(name)
                record.flush()
                os.fsync(record.fileno())
        except FileExistsError:
            raise BusinessRuleError("a sandbox create is already pending") from None
        _sync_directory(fence.parent)
        _sync_directory(fence.parent.parent)

    @staticmethod
    def _clear_create(fence: Path) -> None:
        fence.unlink()
        _sync_directory(fence.parent)

    def _settle_create(
        self,
        dispatch_id: str,
        fence: Path | None,
        name_pattern: str,
        scope: Path | None = None,
    ) -> None:
        if fence is None:
            return
        try:
            name = fence.read_text(encoding="ascii")
        except FileNotFoundError:
            return
        if not re.fullmatch(name_pattern, name):
            raise BusinessRuleError("invalid sandbox create fence")
        with cleanup_budget():
            owned = self._owned(dispatch_id, re.escape(name))
            if len(owned) != 1:
                if (
                    not owned
                    and scope is not None
                    and self._scope_data(scope)["state"] in {"PREPARED", "REJECTED"}
                ):
                    self._clear_create(fence)
                    return
                raise BusinessRuleError(
                    "sandbox Docker create remains uncertain; dispatch stays blocked"
                )
        # The exact owned name is affirmative evidence this create settled.
        # Clear intent before removal so a crash cannot lose that evidence.
        self._clear_create(fence)

    @staticmethod
    def _inspect_image(identifier: str, name: str, dispatch_id: str) -> str:
        raw = _docker(
            [
                "container",
                "inspect",
                "--format",
                "{{json .Id}} {{json .Name}} {{json .Image}} "
                '{{json (index .Config.Labels "' + _DISPATCH_LABEL + '")}}',
                identifier,
            ]
        )
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
        raise BusinessRuleError("could not verify sandbox container identity")

    def _cleanup(self, dispatch_id: str, name_pattern: str) -> None:
        identifiers = self._owned(dispatch_id, name_pattern)
        for identifier in identifiers:
            try:
                _docker(["rm", "-f", identifier])
            except (BusinessRuleError, RuntimeExecutionError):
                # A concurrent removal can still be visible when rm fails.
                if not self._wait_for_removal(dispatch_id, name_pattern, identifier):
                    raise
        if self._owned(dispatch_id, name_pattern):
            raise BusinessRuleError("sandbox termination not confirmed")

    def _wait_for_removal(
        self, dispatch_id: str, name_pattern: str, identifier: str
    ) -> bool:
        try:
            allowance = operation_timeout(3)
            with dispatch_budget(time.time() + allowance):
                while identifier in self._owned(dispatch_id, name_pattern):
                    time.sleep(operation_timeout(0.1))
                return True
        except TimeoutError:
            return False

    def _owned(self, dispatch_id: str, name_pattern: str) -> list[str]:
        raw = _docker(
            [
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
            ]
        )
        identifiers = []
        try:
            for line in raw.splitlines():
                row = json.loads(line)
                if (
                    not re.fullmatch(name_pattern, row["Names"])
                    or not _CONTAINER_ID.fullmatch(row["ID"])
                    or f"{_DISPATCH_LABEL}={dispatch_id}"
                    not in row["Labels"].split(",")
                ):
                    raise ValueError("container ownership mismatch")
                identifiers.append(row["ID"])
        except (AttributeError, KeyError, TypeError, ValueError):
            raise BusinessRuleError("could not verify sandbox ownership") from None
        return identifiers
