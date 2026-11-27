"""Packaging checks for supported runtime modules and resources."""

import importlib
from pathlib import Path

from agent_workflow.interfaces.cli.admin import historical_spec


def test_packaged_reference_available():
    assert len(historical_spec()["acceptance_criteria"]) == 5


def test_retired_packages_and_entrypoints_are_absent():
    source = Path(__file__).resolve().parents[1] / "src"
    assert not (source / "my_wfc").exists()
    assert not (source / "order_service").exists()
    assert not (source / "agent_workflow" / "mcp").exists()
    assert not (source / "agent_workflow" / "coder_runtime.py").exists()
    assert not (source / "agent_workflow" / "runners").exists()
    assert not (source / "agent_workflow" / "bootstrap").exists()
    assert not (source / "agent_workflow" / "integrations").exists()
    assert not (source / "agent_workflow" / "orchestration").exists()
    assert not (source / "agent_workflow" / "infrastructure" / "providers").exists()
    for module in (
        "agent_workflow.mcp",
        "agent_workflow.coder_runtime",
        "agent_workflow.runners",
        "agent_workflow.auth0_oauth",
        "agent_workflow.security",
        "agent_workflow.tools",
        "agent_workflow.server",
        "agent_workflow.bootstrap",
        "agent_workflow.controller",
        "agent_workflow.github",
        "agent_workflow.integrations",
        "agent_workflow.orchestration",
        "agent_workflow.infrastructure.providers",
        "agent_workflow.composition.postgres_ci_entrypoint",
        "agent_workflow.composition.postgres_dispatch_entrypoint",
        "agent_workflow.composition.postgres_dispatch_deps",
    ):
        assert importlib.util.find_spec(module) is None
    assert importlib.util.find_spec("agent_workflow.interfaces.cli.controller")


def test_trusted_worker_imports_from_agent_workflow_package():
    for module in (
        "agent_workflow.workers.trusted_git.main",
        "agent_workflow.workers.trusted_git.app_auth",
        "agent_workflow.workers.trusted_git.repository",
        "agent_workflow.workers.trusted_git.github",
        "agent_workflow.workers.trusted_git.http_transport",
    ):
        assert importlib.import_module(module)
