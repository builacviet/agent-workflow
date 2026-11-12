"""R2-00 frozen V1 behavior before Clean Architecture refactors.

All evidence is local and synthetic: no GitHub, Docker, network, credentials or
production state.
"""

from dataclasses import replace

import pytest
from pydantic import ValidationError

from agent_workflow.domain.contracts import Submission, TaskSpec
from agent_workflow.domain.service import WorkflowService
from agent_workflow.integrations.ci_gate import CiEvidence
from agent_workflow.integrations.github import REPOSITORY, WorkflowError
from agent_workflow.runners.runtime_plugins import (
    CodexCLIPlugin,
    RuntimeManifest,
    RuntimePlan,
    RuntimeRegistry,
    RuntimeRequest,
    RuntimeSpec,
    WorkerResult,
)
from agent_workflow.storage.ci_state import ControllerCiState
from agent_workflow.storage.dispatch_repository import DispatchLedger


BASE, HEAD, REPAIR = "a" * 40, "b" * 40, "c" * 40
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


def handoff(service, task_id="R200", sha=HEAD):
    return service.register_task_handoff(
        task_id, REPOSITORY, BRANCH, BASE, sha, HANDOFF, "offline tests passed"
    )


def review_payload(claim):
    return {
        "task_id": claim["task_id"],
        "claim_token": claim["claim_token"],
        "reviewed_head_sha": claim["reviewed_sha"],
        "review_round": claim["review_round"],
        "verdict": "APPROVED",
        "summary": "Synthetic review only",
        "findings": [],
        "prior_resolutions": {},
        "requested_effort": "medium",
    }


def evidence(verdict, *, sha=HEAD, check_id=43):
    return CiEvidence(
        "R200", REPOSITORY, 17, 31, sha, sha, sha,
        "Python tests", "github-actions", verdict,
        {
            "SUCCESS": "REQUIRED_CHECK_PASSED",
            "FAILURE": "REQUIRED_CHECK_FAILED",
            "BLOCKED": "CHECKS_UNVERIFIED",
        }[verdict],
        check_id, 100 + check_id, 1,
        "2026-11-11T00:00:00Z", "2026-11-11T00:01:00Z",
    )


def test_public_workflow_review_roundtrip_is_durable_and_idempotent(
    tmp_path, monkeypatch
):
    """A migration must preserve public handoff, review and provenance behavior."""
    path = tmp_path / "workflow.db"
    service = WorkflowService(path, require_ci=False)
    service.provision_task(task_spec())
    assert handoff(service) == {
        "task_id": "R200", "status": "READY_IN_REVIEW", "head_sha": HEAD
    }
    before = service.get_review_context("R200")
    assert [event["action"] for event in before["events"]] == [
        "LOCAL_AUTHORIZATION", "HANDOFF"
    ]

    # Re-running a completed handoff against the same SHA must not duplicate it.
    handoff(service)
    restarted = WorkflowService(path, require_ci=False)
    assert len(restarted.get_review_context("R200")["events"]) == 2
    assert restarted.get_review_context("R200")["github_references"]["complete_diff"].endswith(
        BASE + "..." + HEAD
    )

    claim = restarted.claim_review("R200", HEAD, "synthetic-reviewer")
    payload = review_payload(claim)
    accepted = restarted.submit_review(payload, "synthetic-reviewer")
    assert accepted["verdict"] == "APPROVED"
    assert accepted["review_round"] == 1
    duplicate = WorkflowService(path, require_ci=False).submit_review(payload, "synthetic-reviewer")
    assert duplicate["duplicate"] is True
    result = WorkflowService(path, require_ci=False).get_review_result("R200")
    assert result["status"] == "APPROVED"
    assert result["completed_review_rounds"] == 1
    assert result["latest_reviewed_sha"] == HEAD
    assert result["approval_current"] is None
    assert result["remote_verification"] == "not_performed_by_wfc"
    assert [event["action"] for event in result["events"]].count("REVIEW_SUBMITTED") == 1


def test_sticky_ci_failure_survives_success_rerun_and_restart(
    tmp_path, monkeypatch
):
    """A successful rerun on a previously failed SHA cannot enable review."""
    path = tmp_path / "workflow.db"
    service = WorkflowService(path, require_ci=True)
    service.provision_task(task_spec())
    handoff(service)
    state = ControllerCiState(path)
    state.track("R200", 31, HEAD)
    state.track("R200", 31, HEAD)  # duplicate tracking is idempotent
    state.record(evidence("FAILURE", check_id=43))
    state.record(evidence("SUCCESS", check_id=44))

    restarted_state = ControllerCiState(path)
    restarted_service = WorkflowService(path, require_ci=True)
    assert restarted_state.previous_failure("R200", HEAD)
    assert not restarted_state.approved("R200", HEAD)
    assert restarted_state.tracked()[0]["verdict"] == "FAILURE"
    assert restarted_service.list_review_queue() == []
    with pytest.raises(WorkflowError, match="exact-HEAD CI"):
        restarted_service.claim_review("R200", HEAD, "synthetic-reviewer")


def test_review_submission_rechecks_ci_and_does_not_consume_round(
    tmp_path, monkeypatch
):
    """A revoked CI observation after claim must not authorize submission."""
    path = tmp_path / "workflow.db"
    service = WorkflowService(path, require_ci=True)
    service.provision_task(task_spec())
    handoff(service)
    state = ControllerCiState(path)
    state.track("R200", 31, HEAD)
    state.record(evidence("SUCCESS", check_id=51))
    claim = service.claim_review("R200", HEAD, "synthetic-reviewer")

    state.record(evidence("BLOCKED", check_id=52))
    with pytest.raises(WorkflowError, match="exact-HEAD CI"):
        service.submit_review(review_payload(claim), "synthetic-reviewer")
    context = WorkflowService(path, require_ci=True).get_review_context("R200")
    assert context["status"] == "IN_REVIEW"
    assert context["completed_review_rounds"] == 0
    assert context["previous_reviews"] == []


def test_blocked_dispatch_retains_single_slot_across_restart(tmp_path):
    """Unknown external effects must not be retried by a new Controller."""
    path = tmp_path / "workflow.db"
    service = WorkflowService(path, require_ci=True)
    for task_id in ("R200", "R201"):
        service.provision_task(task_spec(task_id))
    ledger = DispatchLedger(service)
    claim = ledger.claim({"R200", "R201"}, 60, track_pre_launch=True)
    assert claim["task_id"] == "R200"
    assert ledger.is_pre_launch(claim["dispatch_id"])
    ledger.block(claim["dispatch_id"])

    restarted = DispatchLedger(WorkflowService(path, require_ci=False))
    assert restarted.is_pre_launch(claim["dispatch_id"])
    assert restarted.claim({"R200", "R201"}, 60) is None
    with pytest.raises(WorkflowError, match="dispatch lease lost"):
        restarted.finish(claim["dispatch_id"], HEAD)
    assert restarted.claim({"R201"}, 60) is None


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
    result = registry.result(
        request, b"synthetic-result", observed_image_id=IMAGE_ID
    )
    assert result.observed_image_id == IMAGE_ID
    assert result.telemetry == "UNVERIFIED"
    with pytest.raises(WorkflowError, match="Dev code phase"):
        registry.plan(replace(request, role="qc", phase="test"))
    with pytest.raises(WorkflowError, match="unsupported runtime capability"):
        registry.plan(replace(request, required_capabilities=frozenset({"host-shell"})))
    with pytest.raises(WorkflowError, match="differs from approved pin"):
        registry.result(request, b"synthetic-result", observed_image_id="sha256:" + "e" * 64)


def test_public_workflow_contract_field_names_remain_stable():
    """R2 may move models but cannot silently mutate MCP DTO field names."""
    assert set(TaskSpec.model_fields) == {
        "task_id", "title", "description", "acceptance_criteria",
        "definition_of_done", "repository", "branch", "base_sha",
        "requested_effort", "risk_level", "require_verified_effort",
        "context_paths", "synthetic",
    }
    assert set(Submission.model_fields) == {
        "task_id", "claim_token", "reviewed_head_sha", "review_round",
        "verdict", "summary", "findings", "prior_resolutions",
        "requested_effort", "actual_effort", "verification_evidence",
    }
    with pytest.raises(ValidationError):
        TaskSpec.model_validate({**task_spec(), "unapproved_extension": True})


def test_ci_failed_head_can_be_replaced_via_public_handoff(tmp_path):
    """Public handoff advances trusted CI tracking before track is replayed."""
    path = tmp_path / "workflow.db"
    service = WorkflowService(path, require_ci=True)
    service.provision_task(task_spec())
    handoff(service)
    state = ControllerCiState(path)
    state.track("R200", 31, HEAD)
    state.record(evidence("FAILURE"))
    assert state.previous_failure("R200", HEAD)

    assert handoff(service, sha=REPAIR)["head_sha"] == REPAIR
    assert state.tracked()[0]["head_sha"] == REPAIR
    state.track("R200", 31, REPAIR)
    assert state.tracked()[0]["repair_heads"] == 1
