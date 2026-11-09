"""Trusted host contracts for selecting and running V1 agent CLI plugins."""

from dataclasses import dataclass, replace
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


def _combination(value: object) -> bool:
    return (
        isinstance(value, tuple)
        and len(value) == 3
        and _identifier(value[0])
        and _text(value[1])
        and (value[2] is None or _identifier(value[2]))
    )


@dataclass(frozen=True)
class RuntimeSpec:
    runtime: str
    provider: str
    model: str
    effort: str | None = None

    def __post_init__(self) -> None:
        if not (
            _identifier(self.runtime)
            and _combination((self.provider, self.model, self.effort))
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
    declared_runtime_version: str
    supported_combinations: frozenset[tuple[str, str, str | None]]
    capabilities: frozenset[str]
    approved_image_id: str | None = None
    api_version: int = 1

    def __post_init__(self) -> None:
        if not (
            _identifier(self.runtime)
            and _text(self.declared_runtime_version)
            and isinstance(self.supported_combinations, frozenset)
            and self.supported_combinations
            and all(map(_combination, self.supported_combinations))
            and _identifiers(self.capabilities)
            and type(self.api_version) is int
        ):
            raise WorkflowError("invalid runtime manifest")
        if self.approved_image_id is not None and not (
            isinstance(self.approved_image_id, str)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", self.approved_image_id)
        ):
            raise WorkflowError("invalid approved runtime image ID")


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
    """Adapter-declared version and independently observed launch identity."""

    request: RuntimeRequest
    declared_runtime_version: str
    exit_code: int = 0
    observed_model: str | None = None
    observed_effort: str | None = None
    telemetry: Literal["UNVERIFIED", "VERIFIED"] = "UNVERIFIED"
    observed_image_id: str | None = None


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
            legacy_codex = (
                manifest.runtime == "codex" and type(plugin) is CodexCLIPlugin
            )
            if manifest.runtime == "codex" and not legacy_codex:
                raise WorkflowError("reserved legacy Codex runtime")
            if not legacy_codex and manifest.approved_image_id is None:
                raise WorkflowError("approved runtime image ID required")
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
        spec = request.spec
        if (spec.provider, spec.model, spec.effort) not in manifest.supported_combinations:
            raise WorkflowError("unsupported runtime provider, model or effort")
        if not request.required_capabilities <= manifest.capabilities:
            raise WorkflowError("unsupported runtime capability")
        return registration

    def approved_image_id(self, request: RuntimeRequest) -> str | None:
        manifest, _ = self._resolve(request)
        return manifest.approved_image_id

    def plan(self, request: RuntimeRequest) -> RuntimePlan:
        _, plugin = self._resolve(request)
        plan = plugin.plan(request)
        if not isinstance(plan, RuntimePlan):
            raise WorkflowError("invalid runtime plan")
        return plan

    def result(
        self,
        request: RuntimeRequest,
        output: bytes,
        *,
        observed_image_id: str | None = None,
    ) -> WorkerResult:
        """Bind launch evidence from the trusted runner after a zero exit code."""
        _, plugin = self._resolve(request)
        if not isinstance(output, bytes):
            raise WorkflowError("invalid runtime output")
        result = plugin.result(request, output)
        if type(result) is not WorkerResult:
            raise WorkflowError("invalid runtime result or provenance")
        if result.observed_image_id is not None:
            raise WorkflowError("runtime adapter cannot attest the launch image")
        result = replace(result, observed_image_id=observed_image_id)
        self.validate_result(request, result, observed_image_id=observed_image_id)
        return result

    def validate_result(
        self,
        request: RuntimeRequest,
        result: WorkerResult,
        *,
        observed_image_id: str | None = None,
    ) -> None:
        manifest, _ = self._resolve(request)
        if observed_image_id != manifest.approved_image_id:
            raise WorkflowError("observed runtime image ID differs from approved pin")
        if not (
            type(result) is WorkerResult
            and result.request == request
            and result.declared_runtime_version == manifest.declared_runtime_version
            and result.observed_image_id == observed_image_id
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

    default_provider = "openai"
    manifest = RuntimeManifest(
        runtime="codex",
        declared_runtime_version="0.161.0",
        # Pinned CLI catalog snapshot in docs/tasks/V1-01.md. The legacy
        # entrypoint leaves effort to Codex and cannot request an explicit value.
        supported_combinations=frozenset(
            ("openai", model, None)
            for model in (
                "gpt-6.1-sol",
                "gpt-6-astra",
                "gpt-6-sol",
                "gpt-6-luna",
                "gpt-5.6-sol",
                "gpt-5.6-terra",
                "gpt-5.6-luna",
                "gpt-5.5",
            )
        ),
        capabilities=frozenset({"workspace-read", "workspace-write"}),
    )

    def plan(self, request: RuntimeRequest) -> RuntimePlan:
        return RuntimePlan(("code",), (("WFC_CODEX_MODEL", request.spec.model),))

    def result(self, request: RuntimeRequest, output: bytes) -> WorkerResult:
        # CLI output is untrusted. The host only observes a successful exit.
        return WorkerResult(request, self.manifest.declared_runtime_version)
