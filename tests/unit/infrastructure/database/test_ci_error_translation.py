"""Keep Application/Domain failures distinct from PostgreSQL failures."""

from unittest.mock import MagicMock, Mock

import pytest
from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from agent_workflow.application.exceptions import (
    InvalidApplicationRequestError,
    PersistenceUnavailableError,
)
from agent_workflow.domain.exceptions import BusinessRuleError
from agent_workflow.infrastructure.database.repositories.ci_state_repository import (
    SqlAlchemyCiStateRepository,
)


class _TypedRecord(BaseModel):
    count: int


def _repository(monkeypatch):
    session = MagicMock()
    scope = MagicMock()
    scope.__enter__.return_value = session
    monkeypatch.setattr(
        "agent_workflow.infrastructure.database.repositories.ci_state_repository."
        "_set_repository_transaction_options",
        Mock(),
    )
    return SqlAlchemyCiStateRepository(lambda: scope), session


@pytest.mark.parametrize(
    "error",
    [
        BusinessRuleError("CI binding mismatch"),
        InvalidApplicationRequestError("invalid CI request"),
    ],
)
def test_ci_write_transaction_preserves_application_and_domain_errors(
    monkeypatch, error
):
    repository, session = _repository(monkeypatch)

    with pytest.raises(type(error)) as caught:
        with repository.transaction():
            raise error

    assert caught.value is error
    session.commit.assert_not_called()


def test_ci_write_transaction_does_not_mislabel_use_case_validation(monkeypatch):
    repository, _ = _repository(monkeypatch)

    with pytest.raises(ValidationError):
        with repository.transaction():
            _TypedRecord(count="invalid")


def test_ci_write_transaction_sanitizes_backend_failures(monkeypatch):
    repository, session = _repository(monkeypatch)
    session.connection.side_effect = SQLAlchemyError("private DSN detail")

    with pytest.raises(
        PersistenceUnavailableError, match="^PostgreSQL Controller CI operation failed$"
    ) as caught:
        with repository.transaction():
            pass

    assert "private DSN" not in str(caught.value)
    assert caught.value.__cause__ is None
