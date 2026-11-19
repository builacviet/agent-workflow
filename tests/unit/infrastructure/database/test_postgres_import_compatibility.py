import subprocess
import sys
from pathlib import Path

import pytest

from agent_workflow.infrastructure.database import models, session_factory
from agent_workflow.storage import postgres_connection, postgres_models


def test_legacy_mappings_share_the_canonical_registry() -> None:
    model_names = (
        "AgentDispatch",
        "AgentExecutionEvidence",
        "CIObservation",
        "CIProcessedDelivery",
        "CITask",
        "Review",
        "ReviewAttempt",
        "ReviewFinding",
        "Task",
        "TaskEvent",
        "WebhookInboxRow",
    )

    assert postgres_models.Base is models.Base
    assert postgres_models.Base.metadata is models.Base.metadata

    mapped_classes = set()
    for name in model_names:
        model = getattr(models, name)
        assert getattr(postgres_models, name) is model
        assert model.__module__ == models.__name__
        assert model.__mapper__.registry is models.Base.registry
        assert models.Base.metadata.tables[model.__tablename__] is model.__table__
        mapped_classes.add(model)

    assert {mapper.class_ for mapper in models.Base.registry.mappers} == mapped_classes
    assert len(models.Base.metadata.tables) == len(model_names)


def test_legacy_factory_shares_the_canonical_types_and_patch_points() -> None:
    assert postgres_connection is session_factory
    assert postgres_connection.PostgresFactory is session_factory.PostgresFactory
    assert (
        postgres_connection.PostgresUnavailableError
        is session_factory.PostgresUnavailableError
    )


@pytest.mark.parametrize("first_path", ["storage", "infrastructure.database"])
def test_postgres_modules_have_no_eager_database_io(first_path: str) -> None:
    script = """
import sys
from importlib import import_module
from unittest.mock import patch

import sqlalchemy
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

paths = {
    "storage": ("postgres_models", "postgres_connection"),
    "infrastructure.database": ("models", "session_factory"),
}
first = sys.argv[1]
second = next(path for path in paths if path != first)
with (
    patch.object(sqlalchemy, "create_engine", side_effect=AssertionError("engine created")),
    patch.object(Engine, "connect", side_effect=AssertionError("DB connected")),
    patch.object(Session, "__init__", side_effect=AssertionError("session created")),
):
    for path in (first, second):
        for module in paths[path]:
            import_module(f"agent_workflow.{path}.{module}")

    legacy = import_module("agent_workflow.storage")
    canonical = import_module("agent_workflow.infrastructure.database")
    assert legacy.postgres_connection is canonical.session_factory
    assert legacy.postgres_models.Base is canonical.models.Base
"""
    result = subprocess.run(
        [sys.executable, "-c", script, first_path],
        cwd=Path(__file__).resolve().parents[4] / "src",
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
