"""Packaging checks for supported runtime modules and resources."""

import importlib
from pathlib import Path

import pytest

from agent_workflow.admin import historical_spec


def test_packaged_reference_available():
    assert len(historical_spec()["acceptance_criteria"]) == 5


@pytest.mark.parametrize(
    ("module", "package"),
    [
        ("coder_runtime", "runners"),
        ("github", "integrations"),
    ],
)
def test_flat_imports_resolve_to_the_same_module(module, package):
    original = importlib.import_module(f"agent_workflow.{module}")
    implementation = importlib.import_module(f"agent_workflow.{package}.{module}")
    assert original is implementation


def test_retired_packages_and_mcp_aliases_are_absent():
    source = Path(__file__).resolve().parents[1] / "src"
    assert not (source / "my_wfc").exists()
    assert not (source / "order_service").exists()
    for module in (
        "agent_workflow.auth0_oauth",
        "agent_workflow.security",
        "agent_workflow.mcp.auth0_oauth",
        "agent_workflow.mcp.security",
        "agent_workflow.tools",
        "agent_workflow.mcp.tools",
        "agent_workflow.server",
        "agent_workflow.mcp.server",
        "agent_workflow.bootstrap.mcp_runtime",
    ):
        assert importlib.util.find_spec(module) is None


def test_trusted_worker_imports_from_agent_workflow_package():
    for module in (
        "agent_workflow.workers.trusted_git.main",
        "agent_workflow.workers.trusted_git.app_auth",
        "agent_workflow.workers.trusted_git.repository",
        "agent_workflow.workers.trusted_git.github",
        "agent_workflow.workers.trusted_git.http_transport",
    ):
        assert importlib.import_module(module)
