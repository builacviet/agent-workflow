"""Offline V1 plugin contract checks using synthetic runtime output."""

from dataclasses import replace

import pytest

from agent_workflow.integrations.github import WorkflowError
from agent_workflow.runners.runtime_plugins import (
    CodexCLIPlugin,
    RuntimeManifest,
    RuntimePlan,
    RuntimeRegistry,
    RuntimeRequest,
    RuntimeSpec,
    WorkerResult,
)


class FakePlugin:
    manifest = RuntimeManifest(
        runtime="fake",
        runtime_version="fixture-1",
        providers=frozenset({"fixture"}),
        capabilities=frozenset({"workspace-read", "workspace-write"}),
        efforts=frozenset({"medium"}),
    )

    def plan(self, request: RuntimeRequest) -> RuntimePlan:
        command = ("fake-exec", request.spec.model)
        if request.spec.effort is not None:
            command += (request.spec.effort,)
        return RuntimePlan(command)

    def result(self, request: RuntimeRequest, output: bytes) -> WorkerResult:
        assert output == b"fixture"
        return WorkerResult(request, self.manifest.runtime_version)


@pytest.fixture
def registry():
    return RuntimeRegistry((CodexCLIPlugin(), FakePlugin()))


@pytest.fixture
def request_spec():
    return RuntimeRequest(
        spec=RuntimeSpec("codex", "openai", "fixture-model"),
        role="dev",
        phase="code",
        dispatch_id="a" * 32,
        input_sha="b" * 40,
        required_capabilities=frozenset({"workspace-write"}),
    )


def test_r01_codex_plugin_registration(registry, request_spec):
    plan = registry.plan(request_spec)
    assert plan.command == ("code",)
    assert plan.env == (("WFC_CODEX_MODEL", "fixture-model"),)
    result = registry.result(request_spec, b"")
    assert result.request == request_spec
    assert result.runtime_version == "0.161.0"
    assert result.exit_code == 0


def test_r02_fake_plugin_substitution(registry, request_spec):
    request = replace(
        request_spec,
        spec=RuntimeSpec("fake", "fixture", "fixture-model", "medium"),
        required_capabilities=frozenset({"workspace-read", "workspace-write"}),
    )
    assert registry.plan(request).command == ("fake-exec", "fixture-model", "medium")
    assert registry.result(request, b"fixture") == WorkerResult(request, "fixture-1")


def test_r03_unsupported_runtime(registry, request_spec):
    request = replace(request_spec, spec=replace(request_spec.spec, runtime="other"))
    with pytest.raises(WorkflowError, match="unsupported runtime$"):
        registry.plan(request)


def test_r04_unsupported_capability(registry, request_spec):
    request = replace(request_spec, required_capabilities=frozenset({"host-shell"}))
    with pytest.raises(WorkflowError, match="unsupported runtime capability"):
        registry.plan(request)


@pytest.mark.parametrize(
    ("role", "phase"),
    [
        ("dev", "prepare"),
        ("dev", "test"),
        ("dev", "publish"),
        ("qc", "code"),
        ("tl-reviewer", "code"),
    ],
)
def test_r05_role_and_phase_preserved(registry, request_spec, role, phase):
    request = replace(request_spec, role=role, phase=phase)
    with pytest.raises(WorkflowError, match="Dev code phase"):
        registry.plan(request)


@pytest.mark.parametrize(
    ("field", "value"),
    [("provider", "other"), ("effort", "medium")],
)
def test_r06_no_silent_fallback(registry, request_spec, field, value):
    request = replace(request_spec, spec=replace(request_spec.spec, **{field: value}))
    with pytest.raises(WorkflowError, match=f"unsupported runtime {field}"):
        registry.plan(request)


def test_r06_adapter_failure_does_not_select_another_plugin(request_spec):
    class FailingPlugin(CodexCLIPlugin):
        def plan(self, request: RuntimeRequest) -> RuntimePlan:
            raise WorkflowError("fixture launch rejected")

    registry = RuntimeRegistry((FailingPlugin(), FakePlugin()))
    with pytest.raises(WorkflowError, match="fixture launch rejected"):
        registry.plan(request_spec)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dispatch_id", "c" * 32),
        ("input_sha", "c" * 40),
        ("spec", RuntimeSpec("codex", "openai", "other-model")),
        ("required_capabilities", frozenset()),
    ],
)
def test_r07_wrong_provenance_rejected(registry, request_spec, field, value):
    wrong_request = replace(request_spec, **{field: value})
    result = WorkerResult(wrong_request, "0.161.0")
    with pytest.raises(WorkflowError, match="invalid runtime result or provenance"):
        registry.validate_result(request_spec, result)


@pytest.mark.parametrize("value", [False, 1, "0"])
def test_r07_invalid_exit_rejected(registry, request_spec, value):
    result = WorkerResult(request_spec, "0.161.0", exit_code=value)
    with pytest.raises(WorkflowError, match="invalid runtime result or provenance"):
        registry.validate_result(request_spec, result)


def test_r07_wrong_adapter_version_rejected(registry, request_spec):
    result = WorkerResult(request_spec, "fixture-1")
    with pytest.raises(WorkflowError, match="invalid runtime result or provenance"):
        registry.validate_result(request_spec, result)


def test_r08_output_cannot_attest_model_or_effort(registry, request_spec):
    output = b'{"model":"fixture-model","effort":"medium","telemetry":"VERIFIED"}'
    result = registry.result(request_spec, output)
    assert result.observed_model is result.observed_effort is None
    assert result.telemetry == "UNVERIFIED"


@pytest.mark.parametrize(
    "telemetry",
    [
        {"telemetry": "VERIFIED"},
        {"observed_model": "fixture-model"},
        {"observed_effort": "medium"},
    ],
)
def test_r08_adapter_cannot_attest_model_or_effort(request_spec, telemetry):
    class UntrustedTelemetryPlugin(CodexCLIPlugin):
        def result(self, request: RuntimeRequest, output: bytes) -> WorkerResult:
            return WorkerResult(request, self.manifest.runtime_version, **telemetry)

    registry = RuntimeRegistry((UntrustedTelemetryPlugin(),))
    with pytest.raises(WorkflowError, match="unverified model or effort telemetry"):
        registry.result(request_spec, b"")


def test_reject_duplicate_and_unknown_interface_version():
    with pytest.raises(WorkflowError, match="duplicate runtime registration"):
        RuntimeRegistry((CodexCLIPlugin(), CodexCLIPlugin()))

    class UnknownInterfacePlugin(CodexCLIPlugin):
        manifest = replace(CodexCLIPlugin.manifest, api_version=2)

    with pytest.raises(WorkflowError, match="unsupported runtime plugin interface"):
        RuntimeRegistry((UnknownInterfacePlugin(),))


@pytest.mark.parametrize(
    ("command", "env"),
    [
        (("prepare",), ()),
        (("publish",), ()),
        (("code",), (("HOME", "/workspace"),)),
        (("code",), (("OPENAI_API_KEY", "synthetic"),)),
        (("code",), (("WFC_CODEX_MODEL", "model\0extra"),)),
        (("code",), (("WFC_CODEX_MODEL", "first"), ("WFC_CODEX_MODEL", "second"))),
    ],
)
def test_invalid_runtime_plan_rejected(command, env):
    with pytest.raises(WorkflowError, match="invalid runtime"):
        RuntimePlan(command, env)
