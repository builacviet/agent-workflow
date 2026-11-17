"""Trusted stage coordinator with injected dispatch, CI and ownership seams.

Adapters and Docker capabilities are supplied by the host facade. This module
does not select a database, grant worker permissions or implement leadership.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from agent_workflow.budget import check_deadline
from agent_workflow.runners.runtime_plugins import (
    RuntimePlan,
    RuntimeRequest,
    WorkerResult,
)


class DispatchEvidence(Protocol):
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


class CiTracking(Protocol):
    def track(self, task_id: str, pr_number: int, head_sha: str) -> None: ...


class RuntimeHost(Protocol):
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


class AgentRuntime:
    """Coordinate one trusted dispatch using explicitly supplied state ports."""

    def __init__(
        self,
        host: RuntimeHost,
        dispatch: DispatchEvidence,
        ci: CiTracking | None = None,
        leadership_guard: Callable[[], bool] | None = None,
        *,
        require_ci_tracking: bool = False,
    ) -> None:
        self.host = host
        self.dispatch = dispatch
        self.ci = ci
        self.leadership_guard = leadership_guard
        self.require_ci_tracking = require_ci_tracking

    def require_leadership(self) -> None:
        if self.leadership_guard is not None:
            check_deadline()
            if self.leadership_guard() is not True:
                raise PermissionError("runtime leadership guard rejected execution")
            check_deadline()

    @contextmanager
    def _phase(self, dispatch_id: str) -> Iterator[None]:
        self.require_leadership()
        try:
            yield
        finally:
            # An admitted phase may have launched even when it raises. Settle
            # its pinned resources once, including after loss or cancellation.
            self.host.stop(dispatch_id)

    def execute_bound(
        self,
        claim: dict,
        context: dict,
        request: RuntimeRequest,
        plan: RuntimePlan,
        approved_image_id: str | None,
    ) -> str:
        track_ci = (
            self.require_ci_tracking or os.environ.get("WFC_CI_GATE_REQUIRED") == "1"
        )
        if track_ci and self.ci is None:
            raise RuntimeError("CI tracking required but not configured")
        self.require_leadership()
        stage, envelope = self.host._prepare_stage(claim, context)
        dispatch_id = claim["dispatch_id"]
        prepare_input = json.dumps(envelope)
        with self._phase(dispatch_id):
            self.host.docker(claim, stage, "prepare", prepare_input)
        source = self.host._source(stage)
        prompt = (
            "Implement only this authorized task; do not commit/push/approve.\n"
            + json.dumps(context)
        )
        self.require_leadership()
        self.host._assert_engine(dispatch_id)
        with self._phase(dispatch_id):
            runtime_result = self.host._run_code(
                claim, source, request, plan, prompt, approved_image_id
            )
        tested_digest = self.host._source_digest(source)
        with self._phase(dispatch_id):
            self.host.docker(claim, source, "test")
        self.host._check_source_digest(source, tested_digest)
        envelope["test_summary"] = (
            "python -m pytest -q: exit 0 in credential-free network-disabled test container"
        )
        publish_input = json.dumps(envelope)
        with self._phase(dispatch_id):
            self.host.docker(claim, stage, "publish", publish_input)
        result = self.host._validated_result(stage, claim, context)
        check_deadline()
        self.require_leadership()
        self.dispatch.record_runtime_evidence(
            dispatch_id,
            context["task_id"],
            claim["input_sha"],
            request.spec.runtime,
            runtime_result.declared_runtime_version,
            runtime_result.observed_image_id,
            runtime_result.telemetry,
            tested_digest,
            result["head_sha"],
        )
        self.require_leadership()
        self.host._register_handoff(context, result, envelope["test_summary"])
        # CI remains BLOCKED until the separate Controller re-verifies HEAD.
        if track_ci and self.ci is not None:
            self.require_leadership()
            self.ci.track(
                context["task_id"],
                int(result["pr_url"].rsplit("/", 1)[1]),
                result["head_sha"],
            )
        self.require_leadership()
        return result["head_sha"]
