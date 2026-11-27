"""Offline characterization of the injected trusted runtime coordinator."""

from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock

import pytest

from agent_workflow.application.dispatch.ports import DispatchClaim
from agent_workflow.application.review.dtos import ReviewTask
from agent_workflow.application.review.use_cases.get_review_context import (
    GetReviewContextOutput,
)
from agent_workflow.application.runtime.dtos import (
    RuntimePlan,
    RuntimeSpec,
    WorkerResult,
)
from agent_workflow.application.runtime.ports import PublishedResult, RuntimeAdapters
from agent_workflow.application.runtime.services.agent_execution import (
    AgentExecutionService,
)
from agent_workflow.application.task.use_cases.get_coder_assignment import (
    CoderAssignmentOutput,
)
from agent_workflow.domain.states import WorkflowStatus
from agent_workflow.domain.workflow_identity import REPOSITORY
from agent_workflow.infrastructure.runtime.worker_payload import (
    worker_assignment_payload,
)

CLAIM = DispatchClaim("a" * 32, "TASK", "b" * 40, 9_999_999_999, 0)
CONTEXT = {
    "task_id": CLAIM.task_id,
    "repository": REPOSITORY,
    "branch": "codex/test",
    "base_sha": CLAIM.input_sha,
}
HEAD = "c" * 40


def assignment_for(context):
    task = ReviewTask(
        task_id=context["task_id"],
        repository=context.get("repository", REPOSITORY),
        branch=context.get("branch", "codex/test"),
        base_sha=context["base_sha"],
        current_head_sha=context.get("current_head_sha"),
        requested_effort="medium",
        require_verified_effort=0,
        max_review_rounds=2,
        completed_review_rounds=context.get("completed_review_rounds", 0),
        status=WorkflowStatus(context.get("status", "AWAITING_HANDOFF")),
        created_at=0,
        updated_at=0,
        context_paths=(),
        spec_json=json.dumps(context),
    )
    return {
        "task_id": task.task_id,
        "base_sha": task.base_sha,
        "current_head_sha": task.current_head_sha,
        "completed_review_rounds": task.completed_review_rounds,
        "status": task.status,
        "details": CoderAssignmentOutput(
            GetReviewContextOutput(task, 1, 2, None, None, (), ()), ()
        ),
    }


class FakeDispatch:
    def __init__(self, calls: list) -> None:
        self.calls = calls

    def is_pre_launch(self, dispatch_id: str) -> bool:
        self.calls.append(("is_pre_launch", dispatch_id))
        return False

    def retire_pre_launch(self, dispatch_id: str) -> None:
        self.calls.append(("retire_pre_launch", dispatch_id))

    def record_runtime_evidence(self, *args) -> None:
        self.calls.append(("evidence", *args))


class FakeCi:
    def __init__(self, calls: list) -> None:
        self.calls = calls

    def execute(self, *args) -> None:
        self.calls.append(("ci", *args))


class FakeRuntime:
    def __init__(self, calls: list, fail_at: str | None = None) -> None:
        self.calls = calls
        self.fail_at = fail_at

    def _call(self, name: str) -> None:
        self.calls.append(name)
        if name == self.fail_at:
            raise RuntimeError(f"uncertain {name}")

    def prepare_stage(self, claim, context):
        self._call("stage")
        return Path("/synthetic"), {
            "context": context,
            "handoff_path": "docs/handoff.md",
        }

    def docker(self, claim, stage, mode, prompt="", *, code_plan=None):
        self._call(mode)
        return b""

    def stop(self, dispatch_id):
        self._call("stop")

    def source(self, stage):
        self._call("source")
        return stage / "source"

    def assert_engine(self, dispatch_id):
        self._call("engine")

    def run_code(self, claim, source, plan, prompt, approved_image_id):
        self._call("code")
        return b"", None

    def source_digest(self, source):
        self._call("digest")
        return "d" * 64

    def check_source_digest(self, source, expected):
        self._call("verify_digest")
        assert expected == "d" * 64

    def validated_result(self, stage, claim, context):
        self._call("validate_result")
        return PublishedResult(HEAD, "https://github.com/example/repo/pull/7")

    def require_leadership(self):
        pass

    def validate_image(self, approved_image_id):
        assert approved_image_id is None

    def bind_engine(self, dispatch_id):
        return nullcontext()

    def release_engine(self, dispatch_id):
        self._call("release_engine")


def make_service(
    runtime, dispatch, ci=None, leadership_guard=None, *, require_ci_tracking
):
    if leadership_guard is not None:

        def require_leadership():
            if leadership_guard() is not True:
                raise PermissionError("runtime leadership guard rejected execution")

        runtime.require_leadership = require_leadership
    registry = Mock()
    registry.plan.return_value = RuntimePlan(("agent",))
    registry.approved_image_id.return_value = None
    registry.result.side_effect = lambda request, *_args, **_kwargs: WorkerResult(
        request=request, declared_runtime_version="1"
    )
    handoff = Mock()
    handoff.execute.side_effect = lambda _: runtime._call("handoff")
    return AgentExecutionService(
        adapter_factory=lambda _guard: RuntimeAdapters(runtime, runtime),
        runtime_spec=lambda: RuntimeSpec("fake", "fixture", "model"),
        registry=registry,
        assignment_payload=worker_assignment_payload,
        dispatch=dispatch,
        handoff_provider=lambda: nullcontext(handoff),
        ci=ci,
        require_ci_tracking=require_ci_tracking,
    )


def run(runtime: AgentExecutionService):
    return runtime.execute(CLAIM, assignment_for(CONTEXT), lambda: None).head_sha


def test_stage_order_and_injected_evidence_ci():
    calls = []
    runtime = make_service(
        FakeRuntime(calls), FakeDispatch(calls), FakeCi(calls), require_ci_tracking=True
    )
    assert run(runtime) == HEAD
    assert calls == [
        "stage",
        "prepare",
        "stop",
        "source",
        "engine",
        "code",
        "stop",
        "digest",
        "test",
        "stop",
        "verify_digest",
        "publish",
        "stop",
        "validate_result",
        (
            "evidence",
            CLAIM.dispatch_id,
            "TASK",
            CLAIM.input_sha,
            "fake",
            "1",
            None,
            "UNVERIFIED",
            "d" * 64,
            HEAD,
        ),
        "handoff",
        ("ci", "TASK", 7, HEAD),
    ]


@pytest.mark.parametrize(
    "failure", ["prepare", "code", "test", "publish", "validate_result"]
)
def test_stage_failure_propagates_and_cleans_up(failure):
    calls = []
    runtime = make_service(
        FakeRuntime(calls, fail_at=failure),
        FakeDispatch(calls),
        require_ci_tracking=False,
    )
    with pytest.raises(RuntimeError, match=f"uncertain {failure}"):
        run(runtime)
    if failure == "validate_result":
        assert calls[-3:] == ["publish", "stop", "validate_result"]
    else:
        assert calls[-2:] == [failure, "stop"]
    assert "handoff" not in calls
    assert not any(isinstance(call, tuple) and call[0] == "evidence" for call in calls)
    assert calls.count("publish") <= 1


def test_leadership_guard_fails_closed_before_stage():
    calls = []
    runtime = make_service(
        FakeRuntime(calls),
        FakeDispatch(calls),
        leadership_guard=lambda: False,
        require_ci_tracking=False,
    )
    with pytest.raises(PermissionError, match="leadership guard"):
        run(runtime)
    assert calls == []


def test_required_ci_port_fails_closed():
    calls = []
    runtime = make_service(
        FakeRuntime(calls), FakeDispatch(calls), require_ci_tracking=True
    )
    with pytest.raises(RuntimeError, match="CI tracking required"):
        run(runtime)
    assert calls == []


def test_leadership_loss_during_evidence_blocks_handoff_and_ci():
    calls = []
    owns_lock = [True]
    dispatch = FakeDispatch(calls)
    record = dispatch.record_runtime_evidence

    def lose_lock_after_evidence(*args):
        record(*args)
        owns_lock[0] = False

    dispatch.record_runtime_evidence = lose_lock_after_evidence
    runtime = make_service(
        FakeRuntime(calls),
        dispatch,
        FakeCi(calls),
        leadership_guard=lambda: owns_lock[0],
        require_ci_tracking=True,
    )
    with pytest.raises(PermissionError, match="leadership guard"):
        run(runtime)
    assert any(isinstance(call, tuple) and call[0] == "evidence" for call in calls)
    assert "handoff" not in calls
    assert not any(isinstance(call, tuple) and call[0] == "ci" for call in calls)
    assert calls.count("publish") == 1
    assert calls.count("stop") == 4


@pytest.mark.parametrize("failed_port", ["evidence", "ci"])
def test_state_port_exception_propagates_without_republish(failed_port):
    calls = []
    dispatch = FakeDispatch(calls)
    ci = FakeCi(calls)

    if failed_port == "evidence":

        def fail_evidence(*args):
            raise RuntimeError("evidence unavailable")

        dispatch.record_runtime_evidence = fail_evidence
    else:

        def fail_ci(*args):
            raise RuntimeError("ci unavailable")

        ci.execute = fail_ci

    runtime = make_service(FakeRuntime(calls), dispatch, ci, require_ci_tracking=True)
    with pytest.raises(RuntimeError, match=f"{failed_port} unavailable"):
        run(runtime)
    assert calls.count("publish") == 1
    assert calls.count("stop") == 4


@pytest.mark.parametrize(
    ("lose_after", "denied"),
    [
        ("stage", "prepare"),
        ("source", "engine"),
        ("engine", "code"),
        ("digest", "test"),
        ("verify_digest", "publish"),
        ("validate_result", "evidence"),
        ("evidence", "handoff"),
        ("handoff", "ci"),
        ("ci", None),
    ],
)
def test_leadership_loss_fences_each_material_boundary(lose_after, denied):
    calls = []
    denied_checks = []

    def guard():
        events = [entry[0] if isinstance(entry, tuple) else entry for entry in calls]
        if lose_after in events:
            denied_checks.append(events)
            return False
        return True

    runtime = make_service(
        FakeRuntime(calls),
        FakeDispatch(calls),
        FakeCi(calls),
        guard,
        require_ci_tracking=True,
    )
    with pytest.raises(PermissionError, match="leadership guard"):
        run(runtime)

    events = [entry[0] if isinstance(entry, tuple) else entry for entry in calls]
    assert denied_checks == [events]
    assert events[-1] == lose_after
    assert denied not in events
    assert events.count("stop") == sum(
        events.count(phase) for phase in ("prepare", "code", "test", "publish")
    )


@pytest.mark.parametrize("failure", [TimeoutError, KeyboardInterrupt])
def test_in_flight_phase_ends_on_deadline_or_cancellation(failure):
    calls = []
    owns_lock = True
    host = FakeRuntime(calls)

    def interrupt(*args):
        nonlocal owns_lock
        calls.append("code")
        owns_lock = False
        raise failure

    host.run_code = interrupt
    runtime = make_service(
        host,
        FakeDispatch(calls),
        leadership_guard=lambda: owns_lock,
        require_ci_tracking=False,
    )
    with pytest.raises(failure):
        run(runtime)
    assert calls[-2:] == ["code", "stop"]
    assert calls.count("stop") == 2
    assert "test" not in calls


def test_uncertain_cleanup_is_not_retried():
    calls = []
    runtime = make_service(
        FakeRuntime(calls, fail_at="stop"),
        FakeDispatch(calls),
        require_ci_tracking=False,
    )
    with pytest.raises(RuntimeError, match="uncertain stop"):
        run(runtime)
    assert calls == ["stage", "prepare", "stop"]
