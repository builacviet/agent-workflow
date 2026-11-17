"""R1-01: immutable Pydantic AppConfig at MCP/Controller boundaries.

Tests never access live GitHub, Docker, credentials or production state.
"""

from argparse import Namespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from agent_workflow.app_config import AppConfig
from agent_workflow.bootstrap.mcp_runtime import postgres_workflow_provider
from agent_workflow.domain.service import WorkflowService, default_service
from agent_workflow.integrations.github import REPOSITORY
from agent_workflow.orchestration import controller
from agent_workflow.storage.postgres_connection import PostgresFactory

BASE = "a" * 40
HEAD = "b" * 40


def _ready(service):
    service.provision_task(
        {
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
        }
    )
    service.register_task_handoff(
        "R101",
        REPOSITORY,
        "codex/r1-01",
        BASE,
        HEAD,
        "docs/handoff.md",
        "synthetic tests passed",
    )


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [(None, True), ("0", False), ("1", True)],
)
def test_loader_defaults_to_ci_required_and_parses_literal_bit(
    monkeypatch,
    env_value,
    expected,
):
    if env_value is None:
        monkeypatch.delenv("WFC_CI_GATE_REQUIRED", raising=False)
    else:
        monkeypatch.setenv("WFC_CI_GATE_REQUIRED", env_value)
    assert AppConfig.from_env().ci_gate_required is expected


@pytest.mark.parametrize("invalid", ["", "false", "true", "yes", "2", " 1", "-1"])
def test_loader_rejects_nonliteral_values(monkeypatch, invalid):
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", invalid)
    with pytest.raises(ValueError, match="WFC_CI_GATE_REQUIRED must be 0 or 1"):
        AppConfig.from_env()


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [(None, True), ("0", False), ("1", True)],
)
def test_mcp_provider_injects_ci_policy(monkeypatch, env_value, expected):
    monkeypatch.setenv(
        "PG_RUNTIME_DSN", "postgresql+psycopg://user:pass@localhost/test"
    )
    monkeypatch.delenv("WFC_POC_NO_AUTH", raising=False)
    factory = Mock()
    monkeypatch.setattr(PostgresFactory, "open", Mock(return_value=factory))
    if env_value is None:
        monkeypatch.delenv("WFC_CI_GATE_REQUIRED", raising=False)
    else:
        monkeypatch.setenv("WFC_CI_GATE_REQUIRED", env_value)

    with postgres_workflow_provider() as service:
        assert service.require_ci is expected
    factory.close.assert_called_once_with()


def test_direct_service_is_fail_closed_even_when_env_requests_bypass(
    tmp_path,
    monkeypatch,
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
    tmp_path,
    monkeypatch,
    env_value,
    expected,
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
    tmp_path,
    monkeypatch,
):
    from agent_workflow.runners import coder_runtime

    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "false")
    monkeypatch.delenv("WFC_POC_NO_AUTH", raising=False)
    path = tmp_path / "controller.db"
    monkeypatch.setenv("WFC_DB_PATH", str(path))
    with pytest.raises(ValueError, match="WFC_CI_GATE_REQUIRED"):
        postgres_workflow_provider()
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


def test_app_config_can_validate_supplied_mapping_without_reading_process_env(
    monkeypatch,
):
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "1")
    local_config = AppConfig.from_env({"WFC_CI_GATE_REQUIRED": "0"})
    assert local_config.ci_gate_required is False
    assert AppConfig.from_env().ci_gate_required is True
    with pytest.raises(ValidationError):
        AppConfig(ci_gate_required="false")
    with pytest.raises(ValidationError):
        AppConfig(unexpected_option=True)
    with pytest.raises(ValidationError):
        local_config.ci_gate_required = True
