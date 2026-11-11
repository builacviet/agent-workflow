"""R1-01: one strict CI Gate env loader at MCP/Controller boundaries.

Tests never access live GitHub, Docker, credentials or production state.
"""

from argparse import Namespace

import pytest

from agent_workflow.ci_gate_config import load_ci_gate_required
from agent_workflow.domain.service import WorkflowService, default_service
from agent_workflow.integrations.ci_gate import CiEvidence
from agent_workflow.integrations.github import REPOSITORY, WorkflowError
from agent_workflow.mcp.tools import configured_service_factory
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


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [(None, True), ("0", False), ("1", True)],
)
def test_loader_defaults_to_ci_required_and_parses_literal_bit(
    monkeypatch, env_value, expected,
):
    if env_value is None:
        monkeypatch.delenv("WFC_CI_GATE_REQUIRED", raising=False)
    else:
        monkeypatch.setenv("WFC_CI_GATE_REQUIRED", env_value)
    assert load_ci_gate_required() is expected


@pytest.mark.parametrize("invalid", ["", "false", "true", "yes", "2", " 1", "-1"])
def test_loader_rejects_nonliteral_values(monkeypatch, invalid):
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", invalid)
    with pytest.raises(ValueError, match="WFC_CI_GATE_REQUIRED must be 0 or 1"):
        load_ci_gate_required()


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [(None, True), ("0", False), ("1", True)],
)
def test_mcp_factory_injects_policy_and_exact_head_check(
    tmp_path, monkeypatch, env_value, expected,
):
    path = tmp_path / "workflow.db"
    monkeypatch.setenv("WFC_DB_PATH", str(path))
    if env_value is None:
        monkeypatch.delenv("WFC_CI_GATE_REQUIRED", raising=False)
    else:
        monkeypatch.setenv("WFC_CI_GATE_REQUIRED", env_value)

    service = configured_service_factory()
    assert service.require_ci is expected
    _ready(service)
    if expected:
        assert service.list_review_queue() == []
        with pytest.raises(WorkflowError, match="exact-HEAD CI"):
            service.claim_review("R101", HEAD, "reviewer")
        _ci_success(path)
    else:
        # Deliberate local bypass without fake GitHub checks.
        assert len(service.list_review_queue()) == 1

    assert service.list_review_queue()[0]["head_sha"] == HEAD
    assert service.claim_review("R101", HEAD, "reviewer")["reviewed_sha"] == HEAD


def test_direct_service_is_fail_closed_even_when_env_requests_bypass(
    tmp_path, monkeypatch,
):
    path = tmp_path / "workflow.db"
    monkeypatch.setenv("WFC_DB_PATH", str(path))
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "0")
    default = WorkflowService(path)
    _ready(default)
    assert default.require_ci is True
    assert default.list_review_queue() == []
    assert default_service().require_ci is True

    local = WorkflowService(path, require_ci=False)
    assert local.require_ci is False
    assert len(local.list_review_queue()) == 1
    # The service policy is captured at construction; no hidden ENV lookups.
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "1")
    assert len(local.list_review_queue()) == 1
    assert default.list_review_queue() == []


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [(None, True), ("0", False), ("1", True)],
)
def test_controller_injects_same_policy_without_docker_launch(
    tmp_path, monkeypatch, env_value, expected,
):
    from agent_workflow.runners import coder_runtime

    if env_value is None:
        monkeypatch.delenv("WFC_CI_GATE_REQUIRED", raising=False)
    else:
        monkeypatch.setenv("WFC_CI_GATE_REQUIRED", env_value)
    seen = []

    class StubRuntime:
        def __init__(self, config, service):
            pass

        def execute(self, claim, context):
            raise AssertionError("fake Controller never executes Docker")

        def release_engine(self, dispatch_id):
            raise AssertionError("fake Controller never releases engines")

    def fake_run_once(service, ledger, task_ids, execute, timeout, **kwargs):
        seen.append(service.require_ci)
        return False

    monkeypatch.setattr(coder_runtime, "CoderRuntime", StubRuntime)
    monkeypatch.setattr(controller, "run_once", fake_run_once)
    controller.operate(
        {"database": str(tmp_path / "controller.db"), "tasks": []},
        Namespace(ci_once=False, ci_watch=False, acknowledge=None, once=True),
    )
    assert seen == [expected]


def test_invalid_env_rejected_before_controller_or_mcp_database_creation(
    tmp_path, monkeypatch,
):
    from agent_workflow.runners import coder_runtime

    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "false")
    path = tmp_path / "controller.db"
    monkeypatch.setenv("WFC_DB_PATH", str(path))
    with pytest.raises(ValueError, match="WFC_CI_GATE_REQUIRED"):
        configured_service_factory()
    assert not path.exists()

    class MustNotLaunch:
        def __init__(self, *args, **kwargs):
            raise AssertionError("invalid config must not launch worker")

    monkeypatch.setattr(coder_runtime, "CoderRuntime", MustNotLaunch)
    with pytest.raises(ValueError, match="WFC_CI_GATE_REQUIRED"):
        controller.operate(
            {"database": str(path), "tasks": []},
            Namespace(ci_once=False, ci_watch=False, acknowledge=None, once=True),
        )
    assert not path.exists()


@pytest.mark.parametrize("invalid", [None, "true", "0", 1, 0, [], {}])
def test_invalid_service_ci_policy_type_fails_before_database(tmp_path, invalid):
    path = tmp_path / "workflow.db"
    with pytest.raises(ValueError, match="require_ci"):
        WorkflowService(path, require_ci=invalid)
    assert not path.exists()
