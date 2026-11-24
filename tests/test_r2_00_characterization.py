"""Retained runtime and public DTO contracts from the R2-00 characterization.

All evidence is local and synthetic: no GitHub, Docker, network, credentials or
production state.
"""

from dataclasses import replace

import pytest
from pydantic import ValidationError

from agent_workflow.integrations.github import REPOSITORY, WorkflowError
from agent_workflow.interfaces.cli.task_spec import ProvisionTaskSpec
from agent_workflow.interfaces.mcp.review_request import Submission
from agent_workflow.runners.runtime_plugins import (
    CodexCLIPlugin,
    RuntimeManifest,
    RuntimePlan,
    RuntimeRegistry,
    RuntimeRequest,
    RuntimeSpec,
    WorkerResult,
)

BASE, HEAD = "a" * 40, "b" * 40
IMAGE_ID = "sha256:" + "d" * 64
BRANCH = "codex/r2-00-fixture"
HANDOFF = "docs/handoff.md"


def task_spec(task_id="R200"):
    return {
        "task_id": task_id,
        "title": "Synthetic R2-00 characterization",
        "description": "No real repository or external effects",
        "acceptance_criteria": ["preserve contract"],
        "definition_of_done": "tests pass",
        "repository": REPOSITORY,
        "branch": BRANCH,
        "base_sha": BASE,
        "requested_effort": "medium",
        "risk_level": "low",
        "require_verified_effort": False,
        "context_paths": [HANDOFF],
        "synthetic": True,
    }


class SyntheticRuntimeAdapter:
    """No Docker or provider credentials: tests the V1 adapter boundary."""

    manifest = RuntimeManifest(
        runtime="synthetic",
        declared_runtime_version="fixture-v1",
        supported_combinations=frozenset({("fixture", "fixture-model", None)}),
        capabilities=frozenset({"workspace-read", "workspace-write"}),
        approved_image_id=IMAGE_ID,
    )

    def plan(self, request: RuntimeRequest) -> RuntimePlan:
        return RuntimePlan(("synthetic-exec", request.spec.model))

    def result(self, request: RuntimeRequest, output: bytes) -> WorkerResult:
        assert output == b"synthetic-result"
        return WorkerResult(request, self.manifest.declared_runtime_version)


def test_runtime_adapter_contract_is_replaceable_without_provider_fallback():
    registry = RuntimeRegistry((CodexCLIPlugin(), SyntheticRuntimeAdapter()))
    request = RuntimeRequest(
        spec=RuntimeSpec("synthetic", "fixture", "fixture-model"),
        role="dev",
        phase="code",
        dispatch_id="d" * 32,
        input_sha=HEAD,
        required_capabilities=frozenset({"workspace-write"}),
    )
    assert registry.plan(request).command == ("synthetic-exec", "fixture-model")
    result = registry.result(request, b"synthetic-result", observed_image_id=IMAGE_ID)
    assert result.observed_image_id == IMAGE_ID
    assert result.telemetry == "UNVERIFIED"
    with pytest.raises(WorkflowError, match="Dev code phase"):
        registry.plan(replace(request, role="qc", phase="test"))
    with pytest.raises(WorkflowError, match="unsupported runtime capability"):
        registry.plan(replace(request, required_capabilities=frozenset({"host-shell"})))
    with pytest.raises(WorkflowError, match="differs from approved pin"):
        registry.result(
            request, b"synthetic-result", observed_image_id="sha256:" + "e" * 64
        )


def test_public_workflow_contract_field_names_remain_stable():
    """R2 may move models but cannot silently mutate MCP DTO field names."""
    assert set(ProvisionTaskSpec.model_fields) == {
        "task_id",
        "title",
        "description",
        "acceptance_criteria",
        "definition_of_done",
        "repository",
        "branch",
        "base_sha",
        "requested_effort",
        "risk_level",
        "require_verified_effort",
        "context_paths",
        "synthetic",
    }
    assert set(Submission.model_fields) == {
        "task_id",
        "claim_token",
        "reviewed_head_sha",
        "review_round",
        "verdict",
        "summary",
        "findings",
        "prior_resolutions",
        "requested_effort",
        "actual_effort",
        "verification_evidence",
    }
    with pytest.raises(ValidationError):
        ProvisionTaskSpec.model_validate({**task_spec(), "unapproved_extension": True})


def test_provision_task_transport_defaults_are_preserved():
    data = task_spec()
    del data["require_verified_effort"]
    del data["synthetic"]
    command = ProvisionTaskSpec.model_validate(data).to_command()
    assert command.spec.require_verified_effort is True
    assert command.spec.synthetic is False
    assert command.spec.acceptance_criteria == ("preserve contract",)
    assert command.spec.context_paths == (HANDOFF,)
