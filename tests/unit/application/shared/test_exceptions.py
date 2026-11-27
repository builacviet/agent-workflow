"""Verify canonical exception inheritance and compatibility imports."""

from agent_workflow.application import exceptions
from agent_workflow.application.shared import errors
from agent_workflow.domain import workflow_identity
from agent_workflow.domain.exceptions import BusinessRuleError, DomainError
from agent_workflow.infrastructure.database import session_factory, unit_of_work


def test_domain_error_retains_legacy_value_error_contract():
    assert BusinessRuleError.__base__ is DomainError
    assert issubclass(BusinessRuleError, ValueError)
    assert not hasattr(workflow_identity, "WorkflowError")


def test_application_errors_have_one_canonical_identity():
    assert errors.ApplicationError is exceptions.ApplicationError
    assert errors.PersistenceError is exceptions.PersistenceError
    assert errors.PersistenceUnavailableError is exceptions.PersistenceUnavailableError
    assert (
        errors.CorruptedPersistenceDataError is exceptions.CorruptedPersistenceDataError
    )
    assert exceptions.PersistenceError.__base__ is exceptions.ApplicationError
    assert (
        exceptions.PersistenceUnavailableError.__base__ is exceptions.PersistenceError
    )
    assert (
        exceptions.CorruptedPersistenceDataError.__base__ is exceptions.PersistenceError
    )
    assert issubclass(exceptions.PersistenceError, RuntimeError)


def test_postgres_error_preserves_legacy_alias_and_canonical_base():
    error = unit_of_work.PostgresUnavailableError
    assert session_factory.PostgresUnavailableError is error
    assert error.__base__ is exceptions.PersistenceUnavailableError
