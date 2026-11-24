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
from agent_workflow.infrastructure.database.session_factory import PostgresFactory


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
    dependencies.handoff_provider = postgres_dispatch_deps.PostgresHandoffProvider(
        factory
    )
    compose = Mock(return_value=dependencies)
    assignment = Mock()
    launch = Mock(side_effect=AssertionError("composition must not launch Worker"))
    monkeypatch.setattr(postgres_dispatch_deps, "compose_postgres_controller", compose)
    monkeypatch.setattr(postgres_dispatch_deps, "GetCoderAssignmentUseCase", assignment)
    monkeypatch.setattr(postgres_dispatch_deps, "compose_postgres_worker", launch)

    postgres_dispatch_deps.create_dispatch_use_case({}, AppConfig.from_env(), factory)
    compose.assert_called_once_with(factory)
    assignment.assert_called_once()
    factory.get_scoped_session.assert_not_called()
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
