"""Offline characterization of the injected trusted runtime coordinator."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_workflow.runners.agent_runtime import AgentRuntime
from agent_workflow.runners.coder_runtime import CoderRuntime

CLAIM = {"dispatch_id": "a" * 32, "input_sha": "b" * 40}
CONTEXT = {"task_id": "TASK"}
HEAD = "c" * 40


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

    def track(self, *args) -> None:
        self.calls.append(("ci", *args))


class FakeHost:
    def __init__(self, calls: list, fail_at: str | None = None) -> None:
        self.calls = calls
        self.fail_at = fail_at

    def _call(self, name: str) -> None:
        self.calls.append(name)
        if name == self.fail_at:
            raise RuntimeError(f"uncertain {name}")

    def _prepare_stage(self, claim, context):
        self._call("stage")
        return Path("/synthetic"), {"context": context}

    def docker(self, claim, stage, mode, prompt="", *, code_plan=None):
        self._call(mode)
        return b""

    def stop(self, dispatch_id):
        self._call("stop")

    def _source(self, stage):
        self._call("source")
        return stage / "source"

    def _assert_engine(self, dispatch_id):
        self._call("engine")

    def _run_code(self, claim, source, request, plan, prompt, approved_image_id):
        self._call("code")
        return SimpleNamespace(
            declared_runtime_version="1",
            observed_image_id=None,
            telemetry="UNVERIFIED",
        )

    def _source_digest(self, source):
        self._call("digest")
        return "d" * 64

    def _check_source_digest(self, source, expected):
        self._call("verify_digest")
        assert expected == "d" * 64

    def _validated_result(self, stage, claim, context):
        self._call("validate_result")
        return {"head_sha": HEAD, "pr_url": "https://github.com/example/repo/pull/7"}

    def _register_handoff(self, context, result, test_summary):
        self._call("handoff")


def run(runtime: AgentRuntime):
    request = SimpleNamespace(spec=SimpleNamespace(runtime="fake"))
    return runtime.execute_bound(CLAIM, CONTEXT, request, SimpleNamespace(), None)


def test_stage_order_and_injected_evidence_ci(monkeypatch):
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "1")
    calls = []
    runtime = AgentRuntime(FakeHost(calls), FakeDispatch(calls), FakeCi(calls))
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
            CLAIM["dispatch_id"],
            "TASK",
            CLAIM["input_sha"],
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
def test_stage_failure_propagates_and_cleans_up(monkeypatch, failure):
    monkeypatch.delenv("WFC_CI_GATE_REQUIRED", raising=False)
    calls = []
    runtime = AgentRuntime(FakeHost(calls, fail_at=failure), FakeDispatch(calls))
    with pytest.raises(RuntimeError, match=f"uncertain {failure}"):
        run(runtime)
    if failure == "validate_result":
        assert calls[-3:] == ["publish", "stop", "validate_result"]
    else:
        assert calls[-2:] == [failure, "stop"]
    assert "handoff" not in calls
    assert not any(isinstance(call, tuple) and call[0] == "evidence" for call in calls)
    assert calls.count("publish") <= 1


def test_leadership_guard_fails_closed_before_stage(monkeypatch):
    monkeypatch.delenv("WFC_CI_GATE_REQUIRED", raising=False)
    calls = []
    runtime = AgentRuntime(
        FakeHost(calls),
        FakeDispatch(calls),
        leadership_guard=lambda: False,
    )
    with pytest.raises(PermissionError, match="leadership guard"):
        run(runtime)
    assert calls == []


def test_required_ci_port_fails_closed(monkeypatch):
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "1")
    calls = []
    runtime = AgentRuntime(FakeHost(calls), FakeDispatch(calls))
    with pytest.raises(RuntimeError, match="CI tracking required"):
        run(runtime)
    assert calls == []


def test_leadership_loss_during_evidence_blocks_handoff_and_ci(monkeypatch):
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "1")
    calls = []
    owns_lock = [True]
    dispatch = FakeDispatch(calls)
    record = dispatch.record_runtime_evidence

    def lose_lock_after_evidence(*args):
        record(*args)
        owns_lock[0] = False

    dispatch.record_runtime_evidence = lose_lock_after_evidence
    runtime = AgentRuntime(
        FakeHost(calls),
        dispatch,
        FakeCi(calls),
        leadership_guard=lambda: owns_lock[0],
    )
    with pytest.raises(PermissionError, match="leadership guard"):
        run(runtime)
    assert any(isinstance(call, tuple) and call[0] == "evidence" for call in calls)
    assert "handoff" not in calls
    assert not any(isinstance(call, tuple) and call[0] == "ci" for call in calls)
    assert calls.count("publish") == 1
    assert calls.count("stop") == 4


@pytest.mark.parametrize("failed_port", ["evidence", "ci"])
def test_state_port_exception_propagates_without_republish(monkeypatch, failed_port):
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "1")
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

        ci.track = fail_ci

    runtime = AgentRuntime(FakeHost(calls), dispatch, ci)
    with pytest.raises(RuntimeError, match=f"{failed_port} unavailable"):
        run(runtime)
    assert calls.count("publish") == 1
    assert calls.count("stop") == 4


def test_legacy_facade_wires_sqlite_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("WFC_SANDBOX_STATE_DIR", str(tmp_path / "fences"))
    runtime = CoderRuntime({}, None)
    assert isinstance(runtime.agent_runtime, AgentRuntime)
    assert runtime.agent_runtime.dispatch.__class__.__name__ == "DispatchLedger"
    assert runtime.agent_runtime.ci.__class__.__name__ == "_SqliteCiTracking"
    assert runtime.agent_runtime.leadership_guard is None


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

    runtime = AgentRuntime(
        FakeHost(calls),
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
    host = FakeHost(calls)

    def interrupt(*args):
        nonlocal owns_lock
        calls.append("code")
        owns_lock = False
        raise failure

    host._run_code = interrupt
    runtime = AgentRuntime(
        host, FakeDispatch(calls), leadership_guard=lambda: owns_lock
    )
    with pytest.raises(failure):
        run(runtime)
    assert calls[-2:] == ["code", "stop"]
    assert calls.count("stop") == 2
    assert "test" not in calls


def test_uncertain_cleanup_is_not_retried():
    calls = []
    runtime = AgentRuntime(FakeHost(calls, fail_at="stop"), FakeDispatch(calls))
    with pytest.raises(RuntimeError, match="uncertain stop"):
        run(runtime)
    assert calls == ["stage", "prepare", "stop"]
