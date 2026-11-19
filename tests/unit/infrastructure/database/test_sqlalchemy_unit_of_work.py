from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from agent_workflow.application.review.use_cases.get_review_context import (
    GetReviewContextInput,
)
from agent_workflow.application.shared.unit_of_work import IUnitOfWork
from agent_workflow.composition import review_deps
from agent_workflow.domain.workflow_identity import WorkflowError
from agent_workflow.infrastructure.database.session_factory import PostgresFactory
from agent_workflow.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork


@pytest.fixture
def scopes():
    factory = Mock(spec=PostgresFactory)
    connection_scope = factory.connect_session.return_value = MagicMock()
    connection = connection_scope.__enter__.return_value
    transaction_scope = connection.begin.return_value
    transaction = transaction_scope.__enter__.return_value
    session_scope = factory.get_sync_session.return_value = MagicMock()
    session = session_scope.__enter__.return_value
    timeline = Mock()
    timeline.attach_mock(session, "session")
    timeline.attach_mock(session_scope, "session_scope")
    timeline.attach_mock(transaction, "transaction")
    timeline.attach_mock(transaction_scope, "transaction_scope")
    timeline.attach_mock(connection_scope, "connection_scope")
    return factory, connection, session, transaction, timeline


def test_commit_is_explicit_deferred_and_has_one_physical_owner(scopes) -> None:
    factory, connection, session, transaction, timeline = scopes
    check = Mock()
    timeline.attach_mock(check, "deadline")
    concrete = SqlAlchemyUnitOfWork(factory, after_session_commit=check)
    unit: IUnitOfWork = concrete
    factory.connect_session.assert_not_called()

    with unit as active:
        assert active is concrete
        assert concrete.session is session
        factory.get_sync_session.assert_called_once_with(connection)
        active.commit()
        session.commit.assert_not_called()
        transaction.commit.assert_not_called()

    session.commit.assert_called_once_with()
    transaction.commit.assert_called_once_with()
    transaction.rollback.assert_not_called()
    calls = [call[0] for call in timeline.mock_calls]
    assert calls.index("session.commit") < calls.index("deadline")
    assert calls.index("deadline") < calls.index("transaction.commit")
    assert calls.index("session_scope.__exit__") < calls.index("transaction.commit")
    assert calls.index("transaction_scope.__exit__") < calls.index(
        "connection_scope.__exit__"
    )


@pytest.mark.parametrize("explicit", [False, True])
def test_scope_without_commit_rolls_back(scopes, explicit: bool) -> None:
    factory, _, session, transaction, _ = scopes
    with SqlAlchemyUnitOfWork(factory) as unit:
        if explicit:
            unit.rollback()
            with pytest.raises(
                RuntimeError, match="already has a transaction decision"
            ):
                unit.commit()

    session.commit.assert_not_called()
    transaction.commit.assert_not_called()
    transaction.rollback.assert_called_once_with()


def test_exception_after_commit_request_leaves_rollback_to_transaction_scope(
    scopes,
) -> None:
    factory, _, session, transaction, timeline = scopes
    error = ValueError("command validation failed")
    unit = SqlAlchemyUnitOfWork(factory)
    with pytest.raises(ValueError) as exc:
        with unit:
            unit.commit()
            raise error

    assert exc.value is error
    session.commit.assert_not_called()
    transaction.commit.assert_not_called()
    exit_calls = [call for call in timeline.mock_calls if "__exit__" in call[0]]
    assert [call[0] for call in exit_calls] == [
        "session_scope.__exit__",
        "transaction_scope.__exit__",
        "connection_scope.__exit__",
    ]
    assert all(call.args[:2] == (ValueError, error) for call in exit_calls)
    with pytest.raises(RuntimeError, match="outside an active scope"):
        unit.commit()


def test_prevents_nested_entry_duplicate_commit_and_read_only_commit(scopes) -> None:
    factory, _, _, transaction, _ = scopes
    unit = SqlAlchemyUnitOfWork(factory)
    with unit:
        with pytest.raises(RuntimeError, match="already active"):
            with unit:
                pytest.fail("entered a second scope")
        factory.connect_session.assert_called_once_with()
        unit.commit()
        with pytest.raises(RuntimeError, match="already has a transaction decision"):
            unit.commit()
    transaction.commit.assert_called_once_with()

    with SqlAlchemyUnitOfWork(factory, read_only=True) as read_only:
        with pytest.raises(RuntimeError, match="read-only"):
            read_only.commit()
    transaction.commit.assert_called_once_with()
    transaction.rollback.assert_called_once_with()


def test_composition_injects_one_session_and_blank_input_never_opens_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = Mock(spec=PostgresFactory)
    uow = MagicMock()
    scoped_uow = uow.__enter__.return_value
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
    scoped_uow.commit.assert_not_called()

    result = use_case.execute(GetReviewContextInput("R2-TEST"))
    assert result.rounds_remaining == 2
    uow_type.assert_called_once_with(factory, read_only=True)
    tasks.assert_called_once_with(scoped_uow.session)
    reviews.assert_called_once_with(scoped_uow.session)
    tasks.return_value.get_task.assert_called_once_with("R2-TEST")
    reviews.return_value.list_reviews.assert_called_once_with("R2-TEST")
    reviews.return_value.list_events.assert_called_once_with("R2-TEST")
    scoped_uow.commit.assert_not_called()
