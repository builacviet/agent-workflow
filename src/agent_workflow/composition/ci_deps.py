"""Layer: Composition
Responsibility: Preflight Controller credentials and scope CI dependencies.
Dependencies: Application CI, PostgreSQL adapters and the read-only GitHub App.
"""

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from agent_workflow.app_config import AppConfig
from agent_workflow.application.ci.ports import (
    ICiBusinessWriteRepository,
    ICiChecksReader,
)
from agent_workflow.application.ci.use_cases.acknowledge_ci_event import (
    AcknowledgeCiEventUseCase,
)
from agent_workflow.application.ci.use_cases.reconcile_ci import ReconcileCiUseCase
from agent_workflow.application.ci.use_cases.record_ci_evidence import (
    RecordCiEvidenceUseCase,
)
from agent_workflow.application.exceptions import InvalidApplicationRequestError
from agent_workflow.application.shared.unit_of_work import IUnitOfWork
from agent_workflow.infrastructure.ci.guarded_adapters import (
    GuardedCiChecks,
    GuardedCiReader,
)
from agent_workflow.infrastructure.ci.leadership_scope import PostgresCiLeadershipScope
from agent_workflow.infrastructure.database.repositories.ci_state_reader import (
    SessionScope,
    SqlAlchemyCiStateReader,
)
from agent_workflow.infrastructure.database.repositories.ci_state_repository import (
    SqlAlchemyCiAcknowledgmentRepository,
    SqlAlchemyCiStateRepository,
)
from agent_workflow.infrastructure.database.session_factory import PostgresFactory
from agent_workflow.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from agent_workflow.integrations.github_app_checks import (
    ControllerAppToken,
    ControllerChecksClient,
)


@dataclass(frozen=True)
class PostgresCiWriteScope:
    """Construct a fresh shared-Session UoW and repository per invocation."""

    factory: PostgresFactory

    @contextmanager
    def __call__(self) -> Iterator[tuple[IUnitOfWork, ICiBusinessWriteRepository]]:
        with self.factory.get_scoped_session() as session:
            yield (
                SqlAlchemyUnitOfWork(session=session, profile="ci_write"),
                SqlAlchemyCiStateRepository(session=session),
            )


def create_ci_state_adapters(
    factory: PostgresFactory,
) -> tuple[SqlAlchemyCiStateReader, SqlAlchemyCiAcknowledgmentRepository]:
    """Wire adapters without a technology-specific dependency bundle.

    Resolve sessions at invocation time for correct late binding, without
    acquiring a database session during dependency assembly.
    """

    def session_scope() -> AbstractContextManager[Session]:
        return factory.get_scoped_session()

    provider: SessionScope = session_scope
    return (
        SqlAlchemyCiStateReader(session_scope=provider),
        SqlAlchemyCiAcknowledgmentRepository(session_scope=provider),
    )


def create_ci_reconcile_use_case(
    factory: PostgresFactory,
    checks: ICiChecksReader,
    *,
    installation_id: int,
) -> ReconcileCiUseCase:
    """Assemble guarded CI Application graph once per command scope."""
    reader, repository = create_ci_state_adapters(factory)
    leadership = PostgresCiLeadershipScope(factory=factory)
    return ReconcileCiUseCase(
        reader=GuardedCiReader(reader=reader, require_owner=leadership.require_owner),
        checks=GuardedCiChecks(checks=checks, require_owner=leadership.require_owner),
        recorder=RecordCiEvidenceUseCase(scope=PostgresCiWriteScope(factory=factory)),
        acknowledger=AcknowledgeCiEventUseCase(repository=repository),
        leadership=leadership,
        installation_id=installation_id,
    )


@contextmanager
def compose_ci_execution(
    config: dict,
    app_config: AppConfig,
    *,
    checks: ICiChecksReader | None = None,
) -> Iterator[ReconcileCiUseCase]:
    """Build the CI use case once and own its dependency lifetime."""
    installation_id, checks = _controller_checks(config, checks)
    with PostgresFactory.open(app_config) as factory:
        yield create_ci_reconcile_use_case(
            factory, checks, installation_id=installation_id
        )


def _controller_checks(
    config: dict, checks: ICiChecksReader | None
) -> tuple[int, ICiChecksReader]:
    """Validate the separate Controller App before opening the database."""
    gate = config.get("controller_github_app")
    if not isinstance(gate, dict):
        raise InvalidApplicationRequestError(
            "separate read-only Controller GitHub App config required"
        )
    app_id, installation_id, pem = (
        gate.get("app_id"),
        gate.get("installation_id"),
        gate.get("pem"),
    )
    if type(app_id) is not int or type(installation_id) is not int or not pem:
        raise InvalidApplicationRequestError("invalid Controller GitHub App config")
    if config.get("pem") and Path(config["pem"]).resolve() == Path(pem).resolve():
        raise InvalidApplicationRequestError(
            "Controller GitHub App must not use worker App PEM"
        )

    if checks is None:
        token = ControllerAppToken(
            app_id=app_id, installation_id=installation_id, pem_path=pem
        )
        checks = ControllerChecksClient(
            installation_id=installation_id, token_provider=token
        )
    return installation_id, checks
