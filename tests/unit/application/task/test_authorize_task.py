"""Exercise the write decision with a fake scoped repository and UoW."""

from dataclasses import replace
from unittest.mock import MagicMock, Mock

import pytest

from agent_workflow.application.task.use_cases.authorize_task import (
    AuthorizeTaskCommand,
    AuthorizeTaskUseCase,
)
from agent_workflow.domain.task.authorization import (
    TaskAuthorization,
    TaskAuthorizationSpec,
)
from agent_workflow.domain.workflow_identity import (
    LEGACY_REPOSITORY,
    REPOSITORY,
    WorkflowError,
)


@pytest.fixture
def command() -> AuthorizeTaskCommand:
    return AuthorizeTaskCommand(
        TaskAuthorizationSpec(
            task_id="R2-EXAMPLE",
            title="Authorize example",
            description="Task description",
            acceptance_criteria=("One result", "Verified"),
            definition_of_done="Verified",
            repository=REPOSITORY,
            branch="codex/example",
            base_sha="a" * 40,
            requested_effort="high",
            risk_level="medium",
            context_paths=("AGENTS.md", "docs/handoff.md"),
        )
    )


class FakeScope:
    def __init__(self):
        self.calls = []
        self.existing = None
        self.pending = None
        self.persisted = None
        self.requested_commit = False
        self.add_error = None
        self.exit_error = None

    def __enter__(self):
        self.calls.append("open")
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            if exc_type is not None:
                return
            if self.exit_error is not None:
                raise self.exit_error
            if self.requested_commit:
                self.persisted = self.pending
                self.calls.append("commit")
                return
        finally:
            self.pending = None
            if self.persisted is None:
                self.calls.append("rollback")

    def load_for_authorization(self, task_id):
        self.calls.append(("load", task_id))
        return self.existing

    def add(self, authorization):
        self.calls.append("add")
        self.pending = authorization
        if self.add_error is not None:
            raise self.add_error

    def commit(self):
        assert not self.requested_commit
        self.requested_commit = True
        self.calls.append("request commit")


@pytest.mark.parametrize("invalid", [None, {}, AuthorizeTaskCommand({})])
def test_rejects_malformed_direct_commands_before_scope(invalid):
    uow = MagicMock()
    repository = Mock()
    with pytest.raises(WorkflowError, match="validated task authorization spec"):
        AuthorizeTaskUseCase(uow, repository).execute(invalid)
    uow.__enter__.assert_not_called()
    repository.load_for_authorization.assert_not_called()


@pytest.mark.parametrize(
    "invalid", [{"repository": "another/repo"}, {"requested_effort": "maximum"}]
)
def test_rejects_unauthorized_direct_inputs_without_side_effects(command, invalid):
    uow = MagicMock()
    repository = Mock()
    with pytest.raises(
        WorkflowError, match="unauthorized repository or unsupported effort"
    ):
        AuthorizeTaskUseCase(uow, repository).execute(
            AuthorizeTaskCommand(replace(command.spec, **invalid))
        )
    uow.__enter__.assert_not_called()
    repository.load_for_authorization.assert_not_called()


def test_persists_then_requests_commit_once(command):
    scope = FakeScope()
    times = iter((10.0, 11.0))
    AuthorizeTaskUseCase(scope, scope, clock=lambda: next(times)).execute(command)

    assert scope.calls == [
        "open",
        ("load", command.spec.task_id),
        "add",
        "request commit",
        "commit",
    ]
    assert scope.persisted == TaskAuthorization(command.spec, 10.0, 11.0)


@pytest.mark.parametrize("conflict", [False, True])
def test_existing_spec_does_not_write_or_commit(command, conflict):
    scope = FakeScope()
    scope.existing = replace(
        command.spec,
        repository=LEGACY_REPOSITORY,
        context_paths=command.spec.context_paths[::-1]
        if conflict
        else command.spec.context_paths,
    )
    clock = Mock()
    use_case = AuthorizeTaskUseCase(scope, scope, clock=clock)
    if conflict:
        with pytest.raises(WorkflowError, match="conflicts with stored metadata"):
            use_case.execute(command)
    else:
        use_case.execute(command)

    assert scope.calls == ["open", ("load", command.spec.task_id), "rollback"]
    clock.assert_not_called()
    assert scope.persisted is None


@pytest.mark.parametrize("deadline", [False, True])
def test_write_or_deadline_failure_rolls_back(command, deadline):
    scope = FakeScope()
    error = (
        TimeoutError("deadline expired") if deadline else RuntimeError("write failed")
    )
    if deadline:
        scope.exit_error = error
    else:
        scope.add_error = error
    with pytest.raises(type(error)) as raised:
        AuthorizeTaskUseCase(scope, scope).execute(command)

    assert raised.value is error
    assert scope.requested_commit is deadline
    assert scope.calls[-1] == "rollback"
    assert scope.persisted is None


@pytest.mark.parametrize(
    "fields",
    [{"task_id": "broken\0id"}, {"branch": "\ud800"}, {"context_paths": ("a\0b",)}],
)
def test_application_delegates_storage_text_checks_to_repository(command, fields):
    scope = FakeScope()
    scope.add_error = WorkflowError("unsupported persistence text")
    invalid = AuthorizeTaskCommand(replace(command.spec, **fields))

    with pytest.raises(WorkflowError, match="unsupported persistence text"):
        AuthorizeTaskUseCase(scope, scope).execute(invalid)

    assert scope.calls == [
        "open",
        ("load", invalid.spec.task_id),
        "add",
        "rollback",
    ]
    assert scope.persisted is None
