"""Layer: Application
Responsibility: Define runtime plugin and execution operation ports.
Dependencies: Application runtime DTOs and Python typing.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import NotRequired, Protocol, TypedDict

from agent_workflow.application.dispatch.ports import DispatchClaim
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


class IDispatchLaunchState(Protocol):
    def is_pre_launch(self, dispatch_id: str) -> bool: ...

    def retire_pre_launch(self, dispatch_id: str) -> None: ...


class IDispatchEvidence(Protocol):
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


class IRuntimeRegistry(Protocol):
    def plan(self, request: RuntimeRequest) -> RuntimePlan: ...

    def approved_image_id(self, request: RuntimeRequest) -> str | None: ...

    def result(
        self,
        request: RuntimeRequest,
        output: bytes,
        *,
        observed_image_id: str | None = None,
    ) -> WorkerResult: ...


class IRuntimeEngine(Protocol):
    def require_leadership(self) -> None: ...

    def validate_image(self, approved_image_id: str | None) -> None: ...

    def bind_engine(self, dispatch_id: str) -> AbstractContextManager[None]: ...

    def assert_engine(self, dispatch_id: str) -> None: ...

    def docker(
        self, claim: DispatchClaim, stage: Path, mode: str, prompt: str = ""
    ) -> bytes: ...

    def run_code(
        self,
        claim: DispatchClaim,
        source: Path,
        plan: RuntimePlan,
        prompt: str,
        approved_image_id: str | None,
    ) -> tuple[bytes, str | None]: ...

    def stop(self, dispatch_id: str) -> None: ...

    def release_engine(self, dispatch_id: str) -> None: ...


class WorkerEnvelope(TypedDict):
    context: dict
    input_sha: str
    deadline: float
    write_paths: list[str]
    handoff_path: str
    app_id: int
    installation_id: int
    test_summary: NotRequired[str]


@dataclass(frozen=True)
class PublishedResult:
    head_sha: str
    pr_url: str


class IRuntimeWorkspace(Protocol):
    def prepare_stage(
        self, claim: DispatchClaim, context: dict
    ) -> tuple[Path, WorkerEnvelope]: ...

    def source(self, stage: Path) -> Path: ...

    def source_digest(self, source: Path) -> str: ...

    def check_source_digest(self, source: Path, expected: str) -> None: ...

    def validated_result(
        self, stage: Path, claim: DispatchClaim, context: dict
    ) -> PublishedResult: ...


@dataclass(frozen=True)
class RuntimeAdapters:
    engine: IRuntimeEngine
    workspace: IRuntimeWorkspace


RuntimeAdapterFactory = Callable[[Callable[[], None]], RuntimeAdapters]
