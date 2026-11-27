"""Runtime deadline and cleanup budget regressions."""

import pytest


def test_cleanup_remains_available_after_deadline(monkeypatch, tmp_path):
    import agent_workflow.budget as budget
    from agent_workflow.infrastructure.runtime import docker_runtime as runtime

    now = [1000]
    monkeypatch.setattr(budget.time, "time", lambda: now[0])

    def command(*args, **kwargs):
        assert budget.operation_timeout(30) == 30
        return b""

    from agent_workflow.infrastructure.runtime import sandbox

    monkeypatch.setattr(sandbox, "docker_command", command)
    monkeypatch.setattr(sandbox, "_require_engine", lambda: "fixture-daemon-a")
    from unittest.mock import Mock

    controller_runtime = runtime.DockerRuntime(
        {},
        dispatch=Mock(),
        leadership_guard=lambda: True,
        sandbox=sandbox.SandboxManager(tmp_path / "sandbox-fences"),
    )
    controller_runtime.sandbox.record_engine(
        "a" * 32, "unix:///fixture.sock", "fixture-daemon-a"
    )
    with budget.dispatch_budget(1001):
        now[0] = 1002
        controller_runtime.stop("a" * 32)
        with pytest.raises(TimeoutError):
            budget.check_deadline()


def test_wall_clock_rollback_does_not_extend_budget(monkeypatch):
    import agent_workflow.budget as budget

    wall, monotonic = [1000], [500]
    monkeypatch.setattr(budget.time, "time", lambda: wall[0])
    monkeypatch.setattr(budget.time, "monotonic", lambda: monotonic[0])
    with budget.dispatch_budget(1010):
        wall[0] = 900
        monotonic[0] = 511
        with pytest.raises(TimeoutError):
            budget.check_deadline()
