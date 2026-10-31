"""Migration-specific packaging and path checks, independent of workflow behavior."""
from pathlib import Path

import pytest

from my_wfc.admin import historical_spec
from my_wfc.paths import database_path


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
    assert "CREATE TABLE IF NOT EXISTS tasks" in files("my_wfc").joinpath("schema.sql").read_text()
