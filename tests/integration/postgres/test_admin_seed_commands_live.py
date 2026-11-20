"""Exercise fixture commands only on an explicitly disposable PostgreSQL database."""

import os
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from agent_workflow import admin
from agent_workflow.app_config import AppConfig
from agent_workflow.application.admin.use_cases import (
    AdminSeedCommand,
    ImportHistoricalReviewsUseCase,
    ResetSyntheticReviewUseCase,
    SeedSyntheticReviewUseCase,
)
from agent_workflow.domain.workflow_identity import WorkflowError
from agent_workflow.infrastructure.database.models import (
    Review,
    ReviewFinding,
    Task,
    TaskEvent,
)
from agent_workflow.infrastructure.database.repositories.admin_seed_repository import (
    SqlAlchemyAdminSeedRepository,
)
from agent_workflow.infrastructure.database.session_factory import PostgresFactory
from agent_workflow.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from agent_workflow.interfaces.cli.task_spec import ProvisionTaskSpec


@pytest.fixture
def disposable_factory():
    dsn = os.environ.get("PG_TEST_DSN")
    if not dsn or os.environ.get("R2_CLI_DISPOSABLE") != "1":
        pytest.skip(
            "requires explicit empty disposable PG_TEST_DSN and R2_CLI_DISPOSABLE=1"
        )
    with PostgresFactory.open(AppConfig(pg_runtime_dsn=dsn)) as factory:
        with factory.connect_session() as connection:
            if connection.scalar(select(func.count()).select_from(Task)):
                pytest.fail("fixture tests require an empty disposable database")
        yield factory


def execute(factory, case, spec):
    with factory.scoped_session() as session:
        case(
            SqlAlchemyUnitOfWork(session), SqlAlchemyAdminSeedRepository(session)
        ).execute(
            AdminSeedCommand(ProvisionTaskSpec.model_validate(spec).to_command().spec)
        )


def snapshot(factory):
    with factory.connect_session() as connection:
        return tuple(
            tuple(
                tuple(row)
                for row in connection.execute(
                    select(table.__table__).order_by(
                        *table.__table__.primary_key.columns
                    )
                )
            )
            for table in (Task, TaskEvent, Review, ReviewFinding)
        )


def test_fixture_replay_concurrency_and_reset_rollback(disposable_factory):
    factory = disposable_factory
    history, synthetic = admin.historical_spec(), admin.synthetic_spec()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(
            pool.map(
                lambda _: execute(factory, ImportHistoricalReviewsUseCase, history),
                range(2),
            )
        )
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(
            pool.map(
                lambda _: execute(factory, SeedSyntheticReviewUseCase, synthetic),
                range(2),
            )
        )
    before = snapshot(factory)
    execute(factory, ImportHistoricalReviewsUseCase, history)
    execute(factory, SeedSyntheticReviewUseCase, synthetic)
    assert snapshot(factory) == before
    assert len(before[0]) == 2 and len(before[1]) == 5
    assert len(before[2]) == 2 and len(before[3]) == 2
    with patch.object(
        SqlAlchemyAdminSeedRepository,
        "seed_synthetic",
        side_effect=RuntimeError("reseed failed"),
    ):
        with pytest.raises(RuntimeError, match="reseed failed"):
            execute(factory, ResetSyntheticReviewUseCase, synthetic)
    assert snapshot(factory) == before
    execute(factory, ResetSyntheticReviewUseCase, synthetic)
    after = snapshot(factory)
    assert len(after[0]) == 2 and len(after[1]) == 5
    assert after[2:] == before[2:]
    with pytest.raises(WorkflowError):
        execute(factory, SeedSyntheticReviewUseCase, synthetic | {"title": "conflict"})
    assert snapshot(factory) == after
