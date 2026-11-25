"""ACK adapter transaction boundaries and sanitized backend failures."""

from unittest.mock import MagicMock, Mock

import pytest
from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from agent_workflow.application.exceptions import (
    InvalidApplicationRequestError,
    PersistenceUnavailableError,
)
from agent_workflow.domain.exceptions import BusinessRuleError
from agent_workflow.infrastructure.database.repositories.ci_state_repository import (
    SqlAlchemyCiAcknowledgmentRepository,
)

DELIVERY = "12345678-1234-1234-1234-123456789abc"


class _TypedRecord(BaseModel):
    count: int


def _repository(monkeypatch):
    session = MagicMock(spec=Session)
    session.scalar.return_value = DELIVERY
    scope = MagicMock()
    scope.__enter__.return_value = session
    configure = Mock()
    monkeypatch.setattr(
        "agent_workflow.infrastructure.database.repositories.ci_state_repository."
        "_set_repository_transaction_options",
        configure,
    )
    repository = SqlAlchemyCiAcknowledgmentRepository(lambda: scope)
    return repository, session, scope, configure


@pytest.mark.parametrize(
    "error",
    [
        BusinessRuleError("CI binding mismatch"),
        InvalidApplicationRequestError("invalid CI request"),
    ],
)
def test_ack_preserves_application_and_domain_errors(monkeypatch, error):
    repository, session, scope, _ = _repository(monkeypatch)
    session.scalar.side_effect = error

    with pytest.raises(type(error)) as caught:
        repository.mark_processed(DELIVERY)

    assert caught.value is error
    session.execute.assert_not_called()
    session.begin.return_value.__exit__.assert_called_once()
    scope.__exit__.assert_called_once()


def test_ack_does_not_mislabel_validation_error(monkeypatch):
    repository, session, _, _ = _repository(monkeypatch)
    session.scalar.side_effect = lambda *_: _TypedRecord(count="invalid")

    with pytest.raises(ValidationError):
        repository.mark_processed(DELIVERY)


def test_ack_sanitizes_backend_failures(monkeypatch):
    repository, session, scope, _ = _repository(monkeypatch)
    session.begin.return_value.__exit__.side_effect = SQLAlchemyError("private DSN detail")

    with pytest.raises(
        PersistenceUnavailableError, match="^PostgreSQL Controller CI operation failed$"
    ) as caught:
        repository.mark_processed(DELIVERY)

    assert "private DSN" not in str(caught.value)
    assert caught.value.__cause__ is None
    scope.__exit__.assert_called_once()


def test_unknown_delivery_rolls_back_without_insert(monkeypatch):
    repository, session, scope, configure = _repository(monkeypatch)
    session.scalar.return_value = None

    assert repository.mark_processed(DELIVERY) is False

    configure.assert_called_once_with(session.connection.return_value, read_only=False)
    session.begin.return_value.__enter__.return_value.rollback.assert_called_once_with()
    session.execute.assert_not_called()
    session.begin.return_value.__exit__.assert_called_once()
    scope.__exit__.assert_called_once()
