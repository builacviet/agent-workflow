"""Layer: Application
Responsibility: Define runtime plugin and execution operation ports.
Dependencies: Application runtime DTOs and Python typing.
"""

from pathlib import Path
from typing import Protocol

from agent_workflow.application.runtime.dtos import (
    RuntimeManifest,
    RuntimePlan,
    RuntimeRequest,
    WorkerResult,
)


class IAgentRuntimePlugin(Protocol):
    """V1 entry point for a trusted runtime adapter."""

    @property
    def manifest(self) -> RuntimeManifest: ...

    def plan(self, request: RuntimeRequest) -> RuntimePlan: ...

    def result(self, request: RuntimeRequest, output: bytes) -> WorkerResult: ...


class IDispatchEvidence(Protocol):
    def is_pre_launch(self, dispatch_id: str) -> bool: ...

    def retire_pre_launch(self, dispatch_id: str) -> None: ...

    def record_runtime_evidence(
        self,
        dispatch_id: str,
        task_id: str,
        input_sha: str,
        runtime_name: str,
        declared_version: str,
        image_id: str | None,
        telemetry: str,
        tested_digest: str,
        published_head_sha: str,
    ) -> None: ...


class ICiTracking(Protocol):
    def execute(self, task_id: str, pr_number: int, head_sha: str) -> None: ...


class IRuntimeHost(Protocol):
    def docker(
        self,
        claim: dict,
        stage: Path,
        mode: str,
        prompt: str = "",
        *,
        code_plan: RuntimePlan | None = None,
    ) -> bytes: ...

    def stop(self, dispatch_id: str) -> None: ...

    def _run_code(
        self,
        claim: dict,
        source: Path,
        request: RuntimeRequest,
        plan: RuntimePlan,
        prompt: str,
        approved_image_id: str | None,
    ) -> WorkerResult: ...

    def _prepare_stage(self, claim: dict, context: dict) -> tuple[Path, dict]: ...

    def _source(self, stage: Path) -> Path: ...

    def _source_digest(self, source: Path) -> str: ...

    def _check_source_digest(self, source: Path, expected: str) -> None: ...

    def _assert_engine(self, dispatch_id: str) -> None: ...

    def _validated_result(self, stage: Path, claim: dict, context: dict) -> dict: ...

    def _register_handoff(
        self, context: dict, result: dict, test_summary: str
    ) -> None: ...
