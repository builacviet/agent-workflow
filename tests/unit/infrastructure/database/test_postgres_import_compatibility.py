import subprocess
import sys
from pathlib import Path

from agent_workflow.infrastructure.database import models
from agent_workflow.infrastructure.database.readers.review_context_reader import (
    SqlAlchemyReviewContextReader,
)


def test_canonical_mappings_share_one_registry() -> None:
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

    mapped_classes = set()
    for name in model_names:
        model = getattr(models, name)
        assert model.__module__ == models.__name__
        assert model.__mapper__.registry is models.Base.registry
        assert models.Base.metadata.tables[model.__tablename__] is model.__table__
        mapped_classes.add(model)

    assert {mapper.class_ for mapper in models.Base.registry.mappers} == mapped_classes
    assert len(models.Base.metadata.tables) == len(model_names)


def test_review_context_reader_uses_canonical_module() -> None:
    assert SqlAlchemyReviewContextReader.__module__ == (
        "agent_workflow.infrastructure.database.readers.review_context_reader"
    )


def test_postgres_modules_have_no_eager_database_io() -> None:
    script = """
from importlib import import_module
from unittest.mock import patch

import sqlalchemy
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

with (
    patch.object(sqlalchemy, "create_engine", side_effect=AssertionError("engine created")),
    patch.object(Engine, "connect", side_effect=AssertionError("DB connected")),
    patch.object(Session, "__init__", side_effect=AssertionError("session created")),
):
    import_module("agent_workflow.infrastructure.database.models")
    import_module("agent_workflow.infrastructure.database.session_factory")
    import_module("agent_workflow.infrastructure.database.readers.review_context_reader")
    import_module("agent_workflow.composition.review_deps")
    import_module("agent_workflow.composition.postgres_mcp")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[4] / "src",
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
