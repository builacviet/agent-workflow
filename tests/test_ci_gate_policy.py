"""R1-01: mandatory operational CI gate, explicit legacy local compatibility.

All tests use disposable SQLite and synthetic CI observations. No GitHub App,
Docker, public endpoint, real credentials or production data are accessed.
"""

from argparse import Namespace

import pytest

from agent_workflow.domain.service import WorkflowService, default_service
from agent_workflow.integrations.ci_gate import CiEvidence
from agent_workflow.integrations.github import REPOSITORY, WorkflowError
from agent_workflow.orchestration import controller
from agent_workflow.storage.ci_state import ControllerCiState


BASE = "a" * 40
HEAD = "b" * 40


def _ready(service):
    service.provision_task({
        "task_id": "R101",
        "title": "Local CI policy fixture",
        "description": "Synthetic policy test",
        "acceptance_criteria": ["CI authorization must be exact-HEAD"],
        "definition_of_done": "CI gate checked",
        "repository": REPOSITORY,
        "branch": "codex/r1-01",
        "base_sha": BASE,
        "requested_effort": "medium",
        "risk_level": "low",
        "require_verified_effort": False,
        "context_paths": ["docs/handoff.md"],
        "synthetic": True,
    })
    service.register_task_handoff(
        "R101", REPOSITORY, "codex/r1-01", BASE, HEAD,
        "docs/handoff.md", "synthetic tests passed",
    )


def _ci_success(path):
    state = ControllerCiState(path)
    state.track("R101", 23, HEAD)
    state.record(CiEvidence(
        "R101", REPOSITORY, 17, 23, HEAD, HEAD, HEAD,
        "Python tests", "github-actions", "SUCCESS", "REQUIRED_CHECK_PASSED",
        901, 902, 1, "2026-11-11T00:00:00Z", "2026-11-11T00:01:00Z",
    ))


@pytest.mark.parametrize("legacy_flag", [None, "0", "false", "1"])
def test_operational_mcp_factory_requires_ci_independent_of_legacy_env(
    tmp_path, monkeypatch, legacy_flag,
):
    path = tmp_path / "workflow.db"
    monkeypatch.setenv("WFC_DB_PATH", str(path))
    if legacy_flag is None:
        monkeypatch.delenv("WFC_CI_GATE_REQUIRED", raising=False)
    else:
        monkeypatch.setenv("WFC_CI_GATE_REQUIRED", legacy_flag)
    service = default_service()
    _ready(service)

    # Neither missing gate schema nor an opt-out string can open review.
    assert service.list_review_queue() == []
    with pytest.raises(WorkflowError, match="exact-HEAD CI"):
        service.claim_review("R101", HEAD, "reviewer")
    _ci_success(path)
    assert service.list_review_queue()[0]["head_sha"] == HEAD
    assert service.claim_review("R101", HEAD, "reviewer")["reviewed_sha"] == HEAD


def test_explicit_test_opt_out_cannot_weaken_independent_production_instance(
    tmp_path, monkeypatch,
):
    path = tmp_path / "workflow.db"
    monkeypatch.setenv("WFC_DB_PATH", str(path))
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "1")
    local = WorkflowService(path, require_ci=False)
    _ready(local)

    # Local compatibility is explicit and per-instance, not process-wide.
    assert len(local.list_review_queue()) == 1
    production = default_service()
    assert production.list_review_queue() == []
    with pytest.raises(WorkflowError, match="exact-HEAD CI"):
        production.claim_review("R101", HEAD, "reviewer")

    # Env changes after composition never disable the strict instance.
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "0")
    assert production.list_review_queue() == []


def test_controller_dispatch_composition_forces_ci_without_loading_docker(
    tmp_path, monkeypatch,
):
    from agent_workflow.runners import coder_runtime

    captured = []
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "0")
    def fake_run_once(service, ledger, task_ids, execute, timeout, **kwargs):
        captured.append(service._ci_gate_required())
        return False

    # The fake runtime has to expose execute when the Controller composes it.
    class StubRuntime:
        def __init__(self, config, service):
            pass

        def execute(self, claim, context):
            raise AssertionError("never execute")

        def release_engine(self, dispatch_id):
            raise AssertionError("never release")

    monkeypatch.setattr(coder_runtime, "CoderRuntime", StubRuntime)
    monkeypatch.setattr(controller, "run_once", fake_run_once)
    controller.operate(
        {"database": str(tmp_path / "controller.db"), "tasks": []},
        Namespace(ci_once=False, ci_watch=False, acknowledge=None, once=True),
    )
    assert captured == [True]


@pytest.mark.parametrize("invalid", ["true", 1, 0, [], {}])
def test_invalid_ci_policy_type_fails_before_creating_database(tmp_path, invalid):
    path = tmp_path / "workflow.db"
    with pytest.raises(ValueError, match="require_ci"):
        WorkflowService(path, require_ci=invalid)
    assert not path.exists()
