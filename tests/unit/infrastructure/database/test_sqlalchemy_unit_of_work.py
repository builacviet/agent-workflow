from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call

import pytest
from sqlalchemy.engine import Connection

from agent_workflow import budget
from agent_workflow.application.review.use_cases.get_review_context import (
    GetReviewContextInput,
)
from agent_workflow.application.shared.errors import PersistenceUnavailableError
from agent_workflow.application.shared.unit_of_work import IUnitOfWork
from agent_workflow.composition import review_deps
from agent_workflow.domain.workflow_identity import WorkflowError
from agent_workflow.infrastructure.database import unit_of_work
from agent_workflow.infrastructure.database.session_factory import (
    PostgresFactory,
    PostgresUnavailableError,
)
from agent_workflow.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork


def test_database_unavailable_error_is_backend_neutral_for_callers():
    assert issubclass(PostgresUnavailableError, PersistenceUnavailableError)


@pytest.fixture
def scopes(monkeypatch):
    monkeypatch.setattr(unit_of_work, "event", Mock())
    connection = Mock(spec=Connection)
    connection.in_transaction.return_value = False
    transaction_scope = connection.begin.return_value = MagicMock()
    transaction = transaction_scope.__enter__.return_value
    session = Mock()
    session.get_bind.return_value = connection
    session.join_transaction_mode = "rollback_only"
    session.in_transaction.return_value = False
    session.connection.return_value = connection
    timeline = Mock()
    timeline.attach_mock(session, "session")
    timeline.attach_mock(transaction, "transaction")
    timeline.attach_mock(transaction_scope, "transaction_scope")
    return connection, session, transaction, timeline


def test_commit_is_explicit_deferred_and_has_one_physical_owner(scopes, monkeypatch):
    connection, session, transaction, timeline = scopes
    deadline = Mock()
    refresh = Mock()
    timeline.attach_mock(deadline, "deadline")
    timeline.attach_mock(refresh, "refresh")
    monkeypatch.setattr(unit_of_work, "set_workflow_query_timeout", refresh)
    concrete = SqlAlchemyUnitOfWork(session, after_session_commit=deadline)
    unit: IUnitOfWork = concrete
    connection.begin.assert_not_called()
    with unit as active:
        assert active is concrete
        assert concrete.session is session
        active.commit()
        session.commit.assert_not_called()
        transaction.commit.assert_not_called()
    session.commit.assert_called_once_with()
    transaction.commit.assert_called_once_with()
    transaction.rollback.assert_not_called()
    assert refresh.call_args_list == [call(session), call(session)]
    calls = [entry[0] for entry in timeline.mock_calls]
    assert calls[calls.index("session.commit") - 1] == "refresh"
    positions = [
        calls.index(name)
        for name in (
            "session.commit",
            "deadline",
            "session.close",
            "transaction.commit",
            "transaction_scope.__exit__",
        )
    ]
    assert positions == sorted(positions)


def test_pre_commit_deadline_failure_never_commits(scopes, monkeypatch):
    connection, session, transaction, _ = scopes
    refresh = Mock(side_effect=[None, TimeoutError("deadline expired")])
    deadline = Mock()
    monkeypatch.setattr(unit_of_work, "set_workflow_query_timeout", refresh)
    with pytest.raises(TimeoutError, match="deadline expired"):
        with SqlAlchemyUnitOfWork(session, after_session_commit=deadline) as unit:
            unit.commit()

    assert refresh.call_args_list == [call(session), call(session)]
    session.commit.assert_not_called()
    deadline.assert_not_called()
    session.close.assert_called_once_with()
    transaction.commit.assert_not_called()
    assert connection.begin.return_value.__exit__.call_args.args[0] is TimeoutError


@pytest.mark.parametrize("explicit", [False, True])
def test_scope_without_commit_rolls_back(scopes, explicit, monkeypatch):
    connection, session, transaction, timeline = scopes
    refresh = Mock()
    deadline = Mock(side_effect=TimeoutError("deadline expired"))
    timeline.attach_mock(deadline, "deadline")
    monkeypatch.setattr(unit_of_work, "set_workflow_query_timeout", refresh)
    monkeypatch.setattr(unit_of_work, "check_deadline", deadline)
    expected = (
        nullcontext()
        if explicit
        else pytest.raises(TimeoutError, match="deadline expired")
    )
    with expected, SqlAlchemyUnitOfWork(session) as unit:
        if explicit:
            unit.rollback()
            with pytest.raises(
                RuntimeError, match="already has a transaction decision"
            ):
                unit.commit()
    session.commit.assert_not_called()
    transaction.commit.assert_not_called()
    transaction.rollback.assert_called_once_with()
    session.close.assert_called_once_with()
    refresh.assert_called_once_with(session)
    assert connection.begin.return_value.__exit__.call_args.args[0] is None
    if explicit:
        deadline.assert_not_called()
    else:
        deadline.assert_called_once_with()
        calls = [entry[0] for entry in timeline.mock_calls]
        assert calls.index("session.close") < calls.index("transaction.rollback")
        assert calls.index("transaction_scope.__exit__") < calls.index("deadline")


@pytest.mark.parametrize("request_commit", [False, True])
def test_exception_leaves_rollback_to_transaction_scope(
    scopes, monkeypatch, request_commit
):
    connection, session, transaction, _ = scopes
    error = ValueError("command validation failed")
    deadline = Mock(side_effect=TimeoutError("deadline expired"))
    monkeypatch.setattr(unit_of_work, "check_deadline", deadline)
    unit = SqlAlchemyUnitOfWork(session)
    with pytest.raises(ValueError) as exc, unit:
        if request_commit:
            unit.commit()
        raise error
    deadline.assert_not_called()
    assert exc.value is error
    session.close.assert_called_once_with()
    session.commit.assert_not_called()
    transaction.commit.assert_not_called()
    assert connection.begin.return_value.__exit__.call_args.args[:2] == (
        ValueError,
        error,
    )
    with pytest.raises(RuntimeError, match="outside an active scope"):
        unit.commit()


def test_prevents_nested_entry_duplicate_commit_and_read_only_commit(scopes):
    connection, session, transaction, _ = scopes
    unit = SqlAlchemyUnitOfWork(session)
    with unit:
        with pytest.raises(RuntimeError, match="already active"), unit:
            pytest.fail("entered a second scope")
        connection.begin.assert_called_once_with()
        unit.commit()
        with pytest.raises(RuntimeError, match="already has a transaction decision"):
            unit.commit()
    with SqlAlchemyUnitOfWork(session, read_only=True) as read_only:
        with pytest.raises(RuntimeError, match="read-only"):
            read_only.commit()
    transaction.commit.assert_called_once_with()
    transaction.rollback.assert_called_once_with()


def test_composition_injects_one_session_and_blank_input_never_opens_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = Mock(spec=PostgresFactory)
    uow = MagicMock()
    session_scope = factory.get_scoped_session.return_value = MagicMock()
    session = session_scope.__enter__.return_value
    uow_type = Mock(return_value=uow)
    tasks = Mock()
    tasks.return_value.get_task.return_value = SimpleNamespace(
        completed_review_rounds=0, max_review_rounds=2
    )
    reviews = Mock()
    reviews.return_value.list_reviews.return_value = ()
    reviews.return_value.list_events.return_value = ()
    monkeypatch.setattr(review_deps, "SqlAlchemyUnitOfWork", uow_type)
    monkeypatch.setattr(review_deps, "SqlAlchemyTaskRepository", tasks)
    monkeypatch.setattr(review_deps, "SqlAlchemyReviewRepository", reviews)
    use_case = review_deps.create_get_review_context_use_case(factory)

    with pytest.raises(WorkflowError, match="task_id must not be blank"):
        use_case.execute(GetReviewContextInput(" \t\n"))

    factory.connect_session.assert_not_called()
    uow_type.assert_not_called()
    tasks.assert_not_called()
    reviews.assert_not_called()
    uow.commit.assert_not_called()

    result = use_case.execute(GetReviewContextInput("R2-TEST"))
    assert result.rounds_remaining == 2
    uow_type.assert_called_once_with(session, read_only=True)
    tasks.assert_called_once_with(session)
    reviews.assert_called_once_with(session)
    tasks.return_value.get_task.assert_called_once_with("R2-TEST")
    reviews.return_value.list_reviews.assert_called_once_with("R2-TEST")
    reviews.return_value.list_events.assert_called_once_with("R2-TEST")
    uow.commit.assert_not_called()


def test_ci_write_profile_uses_fixed_options_without_workflow_hooks(
    scopes, monkeypatch
):
    connection, session, transaction, _ = scopes
    refresh = Mock()
    deadline = Mock()
    options = Mock()
    monkeypatch.setattr(unit_of_work, "set_workflow_query_timeout", refresh)
    monkeypatch.setattr(budget, "operation_timeout", deadline)
    monkeypatch.setattr(unit_of_work, "_set_repository_transaction_options", options)

    with SqlAlchemyUnitOfWork(session, profile="ci_write") as unit:
        options.assert_called_once_with(connection, read_only=False)
        unit.commit()
        transaction.commit.assert_not_called()

    refresh.assert_not_called()
    deadline.assert_not_called()
    unit_of_work.event.listen.assert_not_called()
    unit_of_work.event.remove.assert_not_called()
    session.commit.assert_called_once_with()
    session.close.assert_called_once_with()
    transaction.commit.assert_called_once_with()


def test_invalid_ci_profile_configuration_never_opens_transaction(scopes):
    connection, session, _, _ = scopes
    with pytest.raises(ValueError, match="cannot be read-only"):
        SqlAlchemyUnitOfWork(session, profile="ci_write", read_only=True)
    with pytest.raises(ValueError, match="Unknown transaction profile"):
        SqlAlchemyUnitOfWork(session, profile="unknown")
    connection.begin.assert_not_called()
