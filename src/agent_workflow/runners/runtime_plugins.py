"""Trusted host contracts for selecting and running V1 agent CLI plugins."""

from dataclasses import dataclass
import re
from typing import Literal, Protocol

from agent_workflow.integrations.github import WorkflowError, validate_sha


def _identifier(value: str) -> bool:
    return isinstance(value, str) and bool(
        re.fullmatch(r"[a-z][a-z0-9-]{0,63}", value)
    )


def _text(value: str) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value.isprintable()


def _identifiers(values: frozenset[str]) -> bool:
    return isinstance(values, frozenset) and all(map(_identifier, values))


@dataclass(frozen=True)
class RuntimeSpec:
    runtime: str
    provider: str
    model: str
    effort: str | None = None

    def __post_init__(self) -> None:
        if not (
            _identifier(self.runtime)
            and _identifier(self.provider)
            and _text(self.model)
            and (self.effort is None or _identifier(self.effort))
        ):
            raise WorkflowError("invalid runtime, provider, model or effort")


@dataclass(frozen=True)
class RuntimeRequest:
    spec: RuntimeSpec
    role: str
    phase: str
    dispatch_id: str
    input_sha: str
    required_capabilities: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not (
            isinstance(self.spec, RuntimeSpec)
            and _identifier(self.role)
            and _identifier(self.phase)
            and isinstance(self.dispatch_id, str)
            and re.fullmatch(r"[a-f0-9]{32}", self.dispatch_id)
            and _identifiers(self.required_capabilities)
        ):
            raise WorkflowError("invalid runtime request")
        validate_sha(self.input_sha)


@dataclass(frozen=True)
class RuntimeManifest:
    runtime: str
    runtime_version: str
    providers: frozenset[str]
    capabilities: frozenset[str]
    efforts: frozenset[str] = frozenset()
    api_version: int = 1

    def __post_init__(self) -> None:
        if not (
            _identifier(self.runtime)
            and _text(self.runtime_version)
            and _identifiers(self.providers)
            and self.providers
            and _identifiers(self.capabilities)
            and _identifiers(self.efforts)
            and type(self.api_version) is int
        ):
            raise WorkflowError("invalid runtime manifest")


@dataclass(frozen=True)
class RuntimePlan:
    """Arguments inside the image. The runner owns Docker and credential settings."""

    command: tuple[str, ...]
    env: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not (
            isinstance(self.command, tuple)
            and self.command
            and all(map(_text, self.command))
            and self.command[0] not in {"prepare", "publish", "test", "probe"}
        ):
            raise WorkflowError("invalid runtime command")
        if not isinstance(self.env, tuple):
            raise WorkflowError("invalid runtime environment")
        names = set()
        for pair in self.env:
            if not (
                isinstance(pair, tuple)
                and len(pair) == 2
                and isinstance(pair[0], str)
                and re.fullmatch(r"WFC_(CODEX_MODEL|PLUGIN_[A-Z][A-Z0-9_]*)", pair[0])
                and _text(pair[1])
                and pair[0] not in names
            ):
                raise WorkflowError("invalid runtime environment")
            names.add(pair[0])


@dataclass(frozen=True)
class WorkerResult:
    """A successful agent phase with the registered adapter version and request."""

    request: RuntimeRequest
    runtime_version: str
    exit_code: int = 0
    observed_model: str | None = None
    observed_effort: str | None = None
    telemetry: Literal["UNVERIFIED", "VERIFIED"] = "UNVERIFIED"


class AgentRuntimePlugin(Protocol):
    """V1 entry point for a trusted runtime adapter."""

    @property
    def manifest(self) -> RuntimeManifest: ...

    def plan(self, request: RuntimeRequest) -> RuntimePlan: ...

    def result(self, request: RuntimeRequest, output: bytes) -> WorkerResult: ...


class RuntimeRegistry:
    def __init__(self, plugins: tuple[AgentRuntimePlugin, ...]) -> None:
        self._plugins: dict[str, tuple[RuntimeManifest, AgentRuntimePlugin]] = {}
        for plugin in plugins:
            manifest = getattr(plugin, "manifest", None)
            if not isinstance(manifest, RuntimeManifest) or manifest.api_version != 1:
                raise WorkflowError("unsupported runtime plugin interface")
            if manifest.runtime in self._plugins:
                raise WorkflowError("duplicate runtime registration")
            self._plugins[manifest.runtime] = (manifest, plugin)

    def _resolve(
        self, request: RuntimeRequest
    ) -> tuple[RuntimeManifest, AgentRuntimePlugin]:
        if not isinstance(request, RuntimeRequest):
            raise WorkflowError("invalid runtime request")
        if (request.role, request.phase) != ("dev", "code"):
            raise WorkflowError("runtime plugins require the Dev code phase")
        registration = self._plugins.get(request.spec.runtime)
        if registration is None:
            raise WorkflowError("unsupported runtime")
        manifest, _ = registration
        if request.spec.provider not in manifest.providers:
            raise WorkflowError("unsupported runtime provider")
        if not request.required_capabilities <= manifest.capabilities:
            raise WorkflowError("unsupported runtime capability")
        if request.spec.effort is not None and request.spec.effort not in manifest.efforts:
            raise WorkflowError("unsupported runtime effort")
        return registration

    def plan(self, request: RuntimeRequest) -> RuntimePlan:
        _, plugin = self._resolve(request)
        plan = plugin.plan(request)
        if not isinstance(plan, RuntimePlan):
            raise WorkflowError("invalid runtime plan")
        return plan

    def result(self, request: RuntimeRequest, output: bytes) -> WorkerResult:
        """Normalize output after the trusted runner observes a zero exit code."""
        _, plugin = self._resolve(request)
        if not isinstance(output, bytes):
            raise WorkflowError("invalid runtime output")
        result = plugin.result(request, output)
        self.validate_result(request, result)
        return result

    def validate_result(self, request: RuntimeRequest, result: WorkerResult) -> None:
        manifest, _ = self._resolve(request)
        if not (
            isinstance(result, WorkerResult)
            and result.request == request
            and result.runtime_version == manifest.runtime_version
            and type(result.exit_code) is int
            and result.exit_code == 0
        ):
            raise WorkflowError("invalid runtime result or provenance")
        # This interface has no trusted evidence source for model/effort.
        if (
            result.telemetry != "UNVERIFIED"
            or result.observed_model is not None
            or result.observed_effort is not None
        ):
            raise WorkflowError("unverified model or effort telemetry")


class CodexCLIPlugin:
    """Use the Codex entrypoint in the existing pinned Docker worker."""

    manifest = RuntimeManifest(
        runtime="codex",
        runtime_version="0.161.0",
        providers=frozenset({"openai"}),
        capabilities=frozenset({"workspace-read", "workspace-write"}),
    )

    def plan(self, request: RuntimeRequest) -> RuntimePlan:
        return RuntimePlan(("code",), (("WFC_CODEX_MODEL", request.spec.model),))

    def result(self, request: RuntimeRequest, output: bytes) -> WorkerResult:
        # CLI output is untrusted. The host only observes a successful exit.
        return WorkerResult(request, self.manifest.runtime_version)
