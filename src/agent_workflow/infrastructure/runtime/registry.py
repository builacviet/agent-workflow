"""Layer: Infrastructure
Responsibility: Register runtime adapters and validate plans and launch evidence.
Dependencies: Application runtime contracts, Domain errors and the built-in Codex adapter.
"""

from dataclasses import replace

from agent_workflow.application.runtime.dtos import (
    RuntimeManifest,
    RuntimePlan,
    RuntimeRequest,
    RuntimeSpec,
    WorkerResult,
)
from agent_workflow.application.runtime.ports import IAgentRuntimePlugin
from agent_workflow.domain.exceptions import BusinessRuleError
from agent_workflow.infrastructure.runtime.codex_plugin import CodexCLIPlugin


def runtime_spec_from_config(config: dict) -> RuntimeSpec:
    """Resolve the current operator selection without executing an adapter."""
    runtime = config.get("runtime", "codex")
    provider = config.get(
        "provider", CodexCLIPlugin.default_provider if runtime == "codex" else ""
    )
    return RuntimeSpec(runtime, provider, config["model"], config.get("effort"))


class RuntimeRegistry:
    def __init__(self, plugins: tuple[IAgentRuntimePlugin, ...]) -> None:
        self._plugins: dict[str, tuple[RuntimeManifest, IAgentRuntimePlugin]] = {}
        for plugin in plugins:
            manifest = getattr(plugin, "manifest", None)
            if not isinstance(manifest, RuntimeManifest) or manifest.api_version != 1:
                raise BusinessRuleError("unsupported runtime plugin interface")
            if manifest.runtime in self._plugins:
                raise BusinessRuleError("duplicate runtime registration")
            legacy_codex = (
                manifest.runtime == "codex" and type(plugin) is CodexCLIPlugin
            )
            if manifest.runtime == "codex" and not legacy_codex:
                raise BusinessRuleError("reserved legacy Codex runtime")
            if not legacy_codex and manifest.approved_image_id is None:
                raise BusinessRuleError("approved runtime image ID required")
            self._plugins[manifest.runtime] = (manifest, plugin)

    def _resolve(
        self, request: RuntimeRequest
    ) -> tuple[RuntimeManifest, IAgentRuntimePlugin]:
        if not isinstance(request, RuntimeRequest):
            raise BusinessRuleError("invalid runtime request")
        return self._resolve_selection(
            request.spec,
            role=request.role,
            phase=request.phase,
            required_capabilities=request.required_capabilities,
        )

    def validate_selection(
        self,
        spec: RuntimeSpec,
        *,
        role: str,
        phase: str,
        required_capabilities: frozenset[str],
    ) -> None:
        """Check admission without creating a dispatch or invoking an adapter."""
        self._resolve_selection(
            spec, role=role, phase=phase, required_capabilities=required_capabilities
        )

    def _resolve_selection(
        self,
        spec: RuntimeSpec,
        *,
        role: str,
        phase: str,
        required_capabilities: frozenset[str],
    ) -> tuple[RuntimeManifest, IAgentRuntimePlugin]:
        if not isinstance(spec, RuntimeSpec):
            raise BusinessRuleError("invalid runtime selection")
        if (role, phase) != ("dev", "code"):
            raise BusinessRuleError("runtime plugins require the Dev code phase")
        registration = self._plugins.get(spec.runtime)
        if registration is None:
            raise BusinessRuleError("unsupported runtime")
        manifest, _ = registration
        if (
            spec.provider,
            spec.model,
            spec.effort,
        ) not in manifest.supported_combinations:
            raise BusinessRuleError("unsupported runtime provider, model or effort")
        if not required_capabilities <= manifest.capabilities:
            raise BusinessRuleError("unsupported runtime capability")
        return registration

    def approved_image_id(self, request: RuntimeRequest) -> str | None:
        manifest, _ = self._resolve(request)
        return manifest.approved_image_id

    def plan(self, request: RuntimeRequest) -> RuntimePlan:
        manifest, plugin = self._resolve(request)
        plan = plugin.plan(request)
        if not isinstance(plan, RuntimePlan):
            raise BusinessRuleError("invalid runtime plan")
        if any(key not in manifest.approved_plan_env for key, _ in plan.env):
            raise BusinessRuleError("runtime environment is not explicitly approved")
        # Custom adapters may supply only explicitly approved nonsecret literals.
        if type(plugin) is CodexCLIPlugin:
            approved = {("WFC_CODEX_MODEL", request.spec.model)}
        else:
            approved = manifest.approved_plan_values
        if any(pair not in approved for pair in plan.env):
            raise BusinessRuleError(
                "runtime environment value is not explicitly approved"
            )
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
            raise BusinessRuleError("invalid runtime output")
        result = plugin.result(request, output)
        if type(result) is not WorkerResult:
            raise BusinessRuleError("invalid runtime result or provenance")
        if result.observed_image_id is not None:
            raise BusinessRuleError("runtime adapter cannot attest the launch image")
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
            raise BusinessRuleError(
                "observed runtime image ID differs from approved pin"
            )
        if not (
            type(result) is WorkerResult
            and result.request == request
            and result.declared_runtime_version == manifest.declared_runtime_version
            and result.observed_image_id == observed_image_id
            and type(result.exit_code) is int
            and result.exit_code == 0
        ):
            raise BusinessRuleError("invalid runtime result or provenance")
        # This interface has no trusted evidence source for model/effort.
        if (
            result.telemetry != "UNVERIFIED"
            or result.observed_model is not None
            or result.observed_effort is not None
        ):
            raise BusinessRuleError("unverified model or effort telemetry")
