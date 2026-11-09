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


FAKE_IMAGE_ID = "sha256:" + "b" * 64


class FakePlugin:
    manifest = RuntimeManifest(
        runtime="fake",
        declared_runtime_version="fixture-1",
        supported_combinations=frozenset({
            ("fixture", "fixture-model", "medium"),
            ("fixture", "fixture-fast", "low"),
            ("other", "other-model", "low"),
        }),
        capabilities=frozenset({"workspace-read", "workspace-write"}),
        approved_image_id=FAKE_IMAGE_ID,
    )

    def plan(self, request: RuntimeRequest) -> RuntimePlan:
        command = ("fake-exec", request.spec.model)
        if request.spec.effort is not None:
            command += (request.spec.effort,)
        return RuntimePlan(command)

    def result(self, request: RuntimeRequest, output: bytes) -> WorkerResult:
        assert output == b"fixture"
        return WorkerResult(request, self.manifest.declared_runtime_version)


@pytest.fixture
def registry():
    return RuntimeRegistry((CodexCLIPlugin(), FakePlugin()))


@pytest.fixture
def request_spec():
    return RuntimeRequest(
        spec=RuntimeSpec("codex", "openai", "gpt-6-luna"),
        role="dev",
        phase="code",
        dispatch_id="a" * 32,
        input_sha="b" * 40,
        required_capabilities=frozenset({"workspace-write"}),
    )


def test_r01_codex_plugin_registration(registry, request_spec):
    plan = registry.plan(request_spec)
    assert plan.command == ("code",)
    assert plan.env == (("WFC_CODEX_MODEL", "gpt-6-luna"),)
    result = registry.result(request_spec, b"")
    assert result.request == request_spec
    assert result.declared_runtime_version == "0.161.0"
    assert result.exit_code == 0
    assert registry.approved_image_id(request_spec) is None
    assert result.observed_image_id is None


@pytest.fixture
def fake_request(request_spec):
    return replace(
        request_spec,
        spec=RuntimeSpec("fake", "fixture", "fixture-model", "medium"),
        required_capabilities=frozenset({"workspace-read", "workspace-write"}),
    )


def test_r02_fake_plugin_substitution(registry, fake_request):
    assert registry.plan(fake_request).command == (
        "fake-exec", "fixture-model", "medium"
    )
    assert registry.approved_image_id(fake_request) == FAKE_IMAGE_ID
    result = registry.result(
        fake_request, b"fixture", observed_image_id=FAKE_IMAGE_ID
    )
    assert result == WorkerResult(
        fake_request, "fixture-1", observed_image_id=FAKE_IMAGE_ID
    )
    registry.validate_result(
        fake_request, result, observed_image_id=FAKE_IMAGE_ID
    )
    with pytest.raises(WorkflowError, match="differs from approved pin"):
        registry.validate_result(fake_request, result)


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
    [("provider", "other"), ("model", "unknown-model"), ("effort", "medium")],
)
def test_r06_no_silent_fallback(registry, request_spec, field, value):
    request = replace(request_spec, spec=replace(request_spec.spec, **{field: value}))
    with pytest.raises(WorkflowError, match="unsupported runtime provider, model or effort"):
        registry.plan(request)


@pytest.mark.parametrize(
    ("field", "value"),
    [("provider", "other"), ("model", "fixture-fast"), ("effort", "low")],
)
def test_r06_individually_known_values_do_not_approve_a_tuple(
    registry, fake_request, field, value
):
    request = replace(fake_request, spec=replace(fake_request.spec, **{field: value}))
    with pytest.raises(WorkflowError, match="unsupported runtime provider, model or effort"):
        registry.plan(request)


def test_r06_adapter_failure_does_not_select_another_plugin(fake_request):
    class FailingPlugin(FakePlugin):
        def plan(self, request: RuntimeRequest) -> RuntimePlan:
            raise WorkflowError("fixture launch rejected")

    registry = RuntimeRegistry((CodexCLIPlugin(), FailingPlugin()))
    with pytest.raises(WorkflowError, match="fixture launch rejected"):
        registry.plan(fake_request)


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
def test_r08_adapter_cannot_attest_model_or_effort(fake_request, telemetry):
    class UntrustedTelemetryPlugin(FakePlugin):
        def result(self, request: RuntimeRequest, output: bytes) -> WorkerResult:
            return WorkerResult(request, self.manifest.declared_runtime_version, **telemetry)

    registry = RuntimeRegistry((UntrustedTelemetryPlugin(),))
    with pytest.raises(WorkflowError, match="unverified model or effort telemetry"):
        registry.result(fake_request, b"fixture", observed_image_id=FAKE_IMAGE_ID)


@pytest.mark.parametrize("pin", ["fixture:latest", "sha256:" + "B" * 64])
def test_reject_mutable_or_invalid_approved_image_identity(pin):
    with pytest.raises(WorkflowError, match="invalid approved runtime image ID"):
        replace(FakePlugin.manifest, approved_image_id=pin)


def test_reject_new_plugin_without_pin():
    class UnpinnedPlugin(FakePlugin):
        manifest = replace(FakePlugin.manifest, approved_image_id=None)

    with pytest.raises(WorkflowError, match="approved runtime image ID required"):
        RuntimeRegistry((UnpinnedPlugin(),))


def test_legacy_exemption_is_reserved_for_builtin_adapter():
    class ImpersonatingPlugin(CodexCLIPlugin):
        manifest = replace(CodexCLIPlugin.manifest, approved_image_id=FAKE_IMAGE_ID)

    with pytest.raises(WorkflowError, match="reserved legacy Codex runtime"):
        RuntimeRegistry((ImpersonatingPlugin(),))


@pytest.mark.parametrize("observed", [None, "sha256:" + "c" * 64])
def test_reject_missing_or_mismatched_launch_evidence(registry, fake_request, observed):
    with pytest.raises(WorkflowError, match="differs from approved pin"):
        registry.result(fake_request, b"fixture", observed_image_id=observed)


def test_reject_adapter_claim_of_launch_evidence(fake_request):
    class SpoofingPlugin(FakePlugin):
        def result(self, request: RuntimeRequest, output: bytes) -> WorkerResult:
            return replace(super().result(request, output), observed_image_id=FAKE_IMAGE_ID)

    registry = RuntimeRegistry((SpoofingPlugin(),))
    with pytest.raises(WorkflowError, match="adapter cannot attest the launch image"):
        registry.result(fake_request, b"fixture", observed_image_id=FAKE_IMAGE_ID)


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



def test_runtime_plan_env_requires_manifest_approval(registry, fake_request):
    class EnvPlugin(FakePlugin):
        def plan(self, request):
            return RuntimePlan(("fake-exec",), (("WFC_PLUGIN_TOKEN", "synthetic"),))

    other = RuntimeRegistry((EnvPlugin(),))
    with pytest.raises(WorkflowError, match="environment is not explicitly approved"):
        other.plan(fake_request)
