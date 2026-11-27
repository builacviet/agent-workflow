"""Layer: Infrastructure
Responsibility: Implement the existing Codex CLI runtime adapter.
Dependencies: Application runtime DTOs.
"""

from agent_workflow.application.runtime.dtos import (
    RuntimeManifest,
    RuntimePlan,
    RuntimeRequest,
    WorkerResult,
)


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
        approved_plan_env=frozenset({"WFC_CODEX_MODEL"}),
    )

    def plan(self, request: RuntimeRequest) -> RuntimePlan:
        return RuntimePlan(("code",), (("WFC_CODEX_MODEL", request.spec.model),))

    def result(self, request: RuntimeRequest, output: bytes) -> WorkerResult:
        # CLI output is untrusted. The host only observes a successful exit.
        return WorkerResult(request, self.manifest.declared_runtime_version)
