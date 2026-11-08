"""Migration-specific packaging and path checks, independent of workflow behavior."""
import importlib
from pathlib import Path
import runpy

import pytest

from agent_workflow import paths
from agent_workflow.admin import historical_spec
from agent_workflow.paths import database_path


def test_default_database_path_is_independent_of_cwd(monkeypatch, tmp_path):
    monkeypatch.delenv("WFC_DB_PATH", raising=False)
    expected = Path(__file__).resolve().parents[1] / "data/workflow.sqlite3"
    assert database_path() == expected
    monkeypatch.chdir(tmp_path)
    assert database_path() == expected


def test_database_override_must_be_stable(monkeypatch, tmp_path):
    target = tmp_path / "state.sqlite3"
    monkeypatch.setenv("WFC_DB_PATH", str(target))
    assert database_path() == target
    monkeypatch.setenv("WFC_DB_PATH", "relative/state.sqlite3")
    with pytest.raises(ValueError, match="absolute"):
        database_path()


def test_packaged_reference_and_schema_available():
    from importlib.resources import files

    assert len(historical_spec()["acceptance_criteria"]) == 5
    schema = files("agent_workflow.storage").joinpath("schema.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS tasks" in schema


def test_installed_default_preserves_legacy_state_path(monkeypatch, tmp_path):
    monkeypatch.delenv("WFC_DB_PATH", raising=False)
    monkeypatch.setattr(paths, "__file__", str(tmp_path / "agent_workflow" / "paths.py"))
    assert database_path() == Path.home() / ".my_wfc/data/workflow.sqlite3"


@pytest.mark.parametrize(
    ("module", "package"),
    [
        ("contracts", "domain"),
        ("service", "domain"),
        ("dispatch_repository", "storage"),
        ("repository", "storage"),
        ("controller", "orchestration"),
        ("coder_runtime", "runners"),
        ("github", "integrations"),
        ("auth0_oauth", "mcp"),
        ("security", "mcp"),
        ("server", "mcp"),
        ("tools", "mcp"),
    ],
)
def test_flat_imports_resolve_to_the_same_module(module, package):
    original = importlib.import_module(f"agent_workflow.{module}")
    implementation = importlib.import_module(f"agent_workflow.{package}.{module}")
    assert original is implementation


@pytest.mark.parametrize("module", ["admin", "controller", "server"])
def test_legacy_module_command_forwards_to_current_entrypoint(monkeypatch, module):
    current = importlib.import_module(f"agent_workflow.{module}")
    calls = []
    monkeypatch.setattr(current, "main", lambda: calls.append(module))
    runpy.run_module(f"my_wfc.{module}", run_name="__main__")
    assert calls == [module]
