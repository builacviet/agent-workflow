"""Layer: Application
Responsibility: Define the trusted runtime plugin port.
Dependencies: Application runtime DTOs and Python typing.
"""

from typing import Protocol

from agent_workflow.application.runtime.dtos import (
    RuntimeManifest,
    RuntimePlan,
    RuntimeRequest,
    WorkerResult,
)


class AgentRuntimePlugin(Protocol):
    """V1 entry point for a trusted runtime adapter."""

    @property
    def manifest(self) -> RuntimeManifest: ...

    def plan(self, request: RuntimeRequest) -> RuntimePlan: ...

    def result(self, request: RuntimeRequest, output: bytes) -> WorkerResult: ...
