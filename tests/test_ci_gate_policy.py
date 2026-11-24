"""R1-01: immutable Pydantic AppConfig at MCP/Controller boundaries.

Tests never access live GitHub, Docker, credentials or production state.
"""

import json
import sys
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from agent_workflow.app_config import AppConfig
from agent_workflow.bootstrap import controller_entrypoint as controller
from agent_workflow.bootstrap.mcp_runtime import postgres_review_reads_provider
from agent_workflow.composition import (
    postgres_dispatch_deps,
    postgres_dispatch_entrypoint,
)
from agent_workflow.domain.service import WorkflowService, default_service
from agent_workflow.integrations.github import REPOSITORY
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

    with postgres_review_reads_provider() as (queue, _, _):
        assert queue._require_ci is expected
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
    monkeypatch, env_value, expected
):
    if env_value is None:
        monkeypatch.delenv("WFC_CI_GATE_REQUIRED", raising=False)
    else:
        monkeypatch.setenv("WFC_CI_GATE_REQUIRED", env_value)
    factory = Mock()
    dependencies = Mock()
    from agent_workflow.storage.postgres_workflow import PostgresWorkflowRepository

    dependencies.workflow = PostgresWorkflowRepository(factory)
    compose = Mock(return_value=dependencies)
    assignment = Mock()
    handoff = Mock()
    launch = Mock(side_effect=AssertionError("composition must not launch Worker"))
    monkeypatch.setattr(postgres_dispatch_deps, "compose_postgres_controller", compose)
    monkeypatch.setattr(postgres_dispatch_deps, "GetCoderAssignmentUseCase", assignment)
    monkeypatch.setattr(postgres_dispatch_deps, "RegisterTaskHandoffUseCase", handoff)
    monkeypatch.setattr(postgres_dispatch_deps, "compose_postgres_worker", launch)

    postgres_dispatch_deps.create_dispatch_use_case({}, AppConfig.from_env(), factory)
    compose.assert_called_once_with(factory)
    assignment.assert_called_once()
    handoff.assert_called_once()
    launch.assert_not_called()


def test_invalid_env_rejected_before_controller_or_mcp_database_creation(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "false")
    monkeypatch.delenv("WFC_POC_NO_AUTH", raising=False)
    monkeypatch.setenv(
        "PG_RUNTIME_DSN", "postgresql+psycopg://user:pass@localhost/test"
    )
    open_database = Mock(side_effect=AssertionError("database must not open"))
    monkeypatch.setattr(PostgresFactory, "open", open_database)
    compose = Mock(side_effect=AssertionError("invalid config must not compose"))
    monkeypatch.setattr(
        postgres_dispatch_entrypoint, "compose_dispatch_execution", compose
    )
    config = tmp_path / "controller.json"
    config.write_text(json.dumps({}))
    monkeypatch.setattr(sys, "argv", ["controller", str(config), "--once"])

    with pytest.raises(ValueError, match="WFC_CI_GATE_REQUIRED"):
        postgres_review_reads_provider()
    with pytest.raises(SystemExit) as error:
        controller.main()

    assert error.value.code == 1
    assert "configuration" in capsys.readouterr().err
    open_database.assert_not_called()
    compose.assert_not_called()


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
