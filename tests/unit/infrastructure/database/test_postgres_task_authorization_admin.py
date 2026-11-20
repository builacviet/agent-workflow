"""Verify the explicit local admin entrypoint and PostgreSQL factory lifetime."""

from collections.abc import Iterator
import json
import sys
from typing import get_type_hints
from unittest.mock import MagicMock, Mock

import pytest
from pydantic import ValidationError

from agent_workflow import admin
from agent_workflow.application.admin.ports import IAdminSeedUseCase
from agent_workflow.application.shared.errors import (
    CorruptedPersistenceDataError,
    PersistenceUnavailableError,
)
from agent_workflow.composition import task_deps
from agent_workflow.domain import service as legacy_service
from agent_workflow.domain.workflow_identity import LEGACY_REPOSITORY, REPOSITORY
from agent_workflow.interfaces.cli.task_spec import ProvisionTaskSpec

LOCAL_DSN = "postgresql+psycopg://test:disposable@127.0.0.1/fixture"


@pytest.fixture
def spec_file(tmp_path):
    path = tmp_path / "spec.json"
    task_spec = {
        "task_id": "LOCAL-TEST",
        "title": "Authorize local test",
        "description": "Example",
        "acceptance_criteria": ["One task", "One event"],
        "definition_of_done": "Observed",
        "repository": LEGACY_REPOSITORY,
        "branch": "codex/example",
        "base_sha": "a" * 40,
        "requested_effort": "high",
        "risk_level": "medium",
        "context_paths": ["AGENTS.md"],
    }
    path.write_text(json.dumps(task_spec), encoding="utf-8")
    return path


@pytest.fixture
def entrypoint(monkeypatch, spec_file):
    monkeypatch.delenv("PGHOSTADDR", raising=False)
    monkeypatch.delenv("PGSERVICE", raising=False)
    legacy = Mock()
    open_factory = Mock()
    monkeypatch.setattr(legacy_service, "default_service", legacy)
    monkeypatch.setattr(task_deps.PostgresFactory, "open", open_factory)

    def run(action="provision", *, postgres=False):
        argv = ["agent_workflow.admin", action, "--file", str(spec_file)]
        if postgres:
            argv.append("--postgres")
        monkeypatch.setattr(sys, "argv", argv)
        admin.main()

    return run, legacy, open_factory


def test_deprecated_postgres_help_is_clear(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["agent_workflow.admin", "--help"])
    with pytest.raises(SystemExit) as result:
        admin.main()
    assert result.value.code == 0
    assert (
        "deprecated no-op: local admin commands always use PostgreSQL"
        in capsys.readouterr().out
    )


@pytest.mark.parametrize("postgres", [False, True])
def test_deprecated_postgres_flag_does_not_select_backend_for_provision(
    entrypoint, monkeypatch, postgres
):
    run, legacy, open_factory = entrypoint
    monkeypatch.setenv("PG_RUNTIME_DSN", LOCAL_DSN)
    open_factory.return_value = MagicMock()
    build = MagicMock()
    monkeypatch.setattr(task_deps, "create_authorize_task_use_case", build)

    run(postgres=postgres)

    open_factory.assert_called_once()
    build.return_value.__enter__.return_value.execute.assert_called_once()
    legacy.assert_not_called()


@pytest.mark.parametrize("action", ["seed-history", "seed-synthetic", "reset-synthetic"])
@pytest.mark.parametrize("postgres", [False, True])
def test_deprecated_postgres_flag_is_ignored_for_seed_and_reset(
    entrypoint, monkeypatch, action, postgres
):
    run, legacy, _ = entrypoint
    scope = MagicMock()
    monkeypatch.setattr(admin, "admin_seed_scope", scope)

    run(action=action, postgres=postgres)

    scope.assert_called_once_with(action)
    scope.return_value.__enter__.return_value.execute.assert_called_once()
    legacy.assert_not_called()


def test_admin_seed_scope_has_typed_use_case_contract():
    assert get_type_hints(task_deps.admin_seed_scope.__wrapped__)["return"] == (
        Iterator[IAdminSeedUseCase]
    )


@pytest.mark.parametrize(
    "dsn",
    [None, "", "sqlite:///fixture.db", LOCAL_DSN.replace("127.0.0.1", "db.example")],
)
def test_missing_invalid_or_remote_dsn_fails_closed(
    entrypoint, dsn, monkeypatch, capsys
):
    run, legacy, open_factory = entrypoint
    if dsn is None:
        monkeypatch.delenv("PG_RUNTIME_DSN", raising=False)
    else:
        monkeypatch.setenv("PG_RUNTIME_DSN", dsn)

    with pytest.raises(SystemExit) as error:
        run()

    assert error.value.code == 2
    stderr = capsys.readouterr().err
    assert "PG_RUNTIME_DSN" in stderr
    assert "disposable@" not in stderr
    legacy.assert_not_called()
    open_factory.assert_not_called()


@pytest.mark.parametrize("override", ["PGHOSTADDR", "PGSERVICE"])
def test_libpq_host_overrides_are_rejected(entrypoint, monkeypatch, capsys, override):
    run, legacy, open_factory = entrypoint
    monkeypatch.setenv("PG_RUNTIME_DSN", LOCAL_DSN)
    monkeypatch.setenv(override, "unexpected")
    with pytest.raises(SystemExit) as error:
        run()

    assert error.value.code == 2
    assert "PGHOSTADDR and PGSERVICE are unsupported" in capsys.readouterr().err
    legacy.assert_not_called()
    open_factory.assert_not_called()


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"synthetic": "false"}, "invalid TaskSpec JSON"),
        ({"extra": True}, "invalid TaskSpec JSON"),
        ({"repository": "another/repo"}, "unauthorized repository"),
        ({"task_id": "bad\0value"}, "invalid TaskSpec JSON"),
    ],
)
def test_bad_transport_or_business_input_does_not_open_database(
    entrypoint, spec_file, updates, message, monkeypatch, capsys
):
    run, legacy, open_factory = entrypoint
    monkeypatch.setenv("PG_RUNTIME_DSN", LOCAL_DSN)
    payload = json.loads(spec_file.read_text(encoding="utf-8"))
    spec_file.write_text(json.dumps(payload | updates), encoding="utf-8")

    with pytest.raises(SystemExit) as error:
        run()

    assert error.value.code == 2
    assert message in capsys.readouterr().err
    legacy.assert_not_called()
    open_factory.assert_not_called()


@pytest.mark.parametrize(
    ("updates", "error"),
    [
        ({"task_id": "bad\0value"}, "task_id must not contain NUL"),
        ({"title": "bad\0title"}, "title must not contain NUL"),
        ({"description": "\ud800"}, "description must be valid UTF-8"),
        (
            {"acceptance_criteria": ["good", "bad\0criteria"]},
            "acceptance_criteria must not contain NUL",
        ),
        (
            {"context_paths": ["good.md", "bad\0path"]},
            "context_paths must not contain NUL",
        ),
    ],
)
def test_provision_task_spec_pydantic_validates_storage_text(spec_file, updates, error):
    payload = json.loads(spec_file.read_text(encoding="utf-8"))
    with pytest.raises(ValidationError, match=error):
        ProvisionTaskSpec.model_validate(payload | updates)


@pytest.mark.parametrize(
    "failure", [None, PersistenceUnavailableError, CorruptedPersistenceDataError]
)
def test_provision_passes_validated_command_and_closes_factory(
    entrypoint, monkeypatch, failure, capsys
):
    run, legacy, open_factory = entrypoint
    monkeypatch.setenv("PG_RUNTIME_DSN", LOCAL_DSN)
    factory_context = MagicMock()
    factory = factory_context.__enter__.return_value
    open_factory.return_value = factory_context
    build = MagicMock()
    monkeypatch.setattr(task_deps, "create_authorize_task_use_case", build)
    use_case = build.return_value.__enter__.return_value
    if failure is not None:
        use_case.execute.side_effect = failure("database failed")
        with pytest.raises(SystemExit) as error:
            run()
        assert error.value.code == 2
        assert "database failed" in capsys.readouterr().err
    else:
        run()

    open_factory.assert_called_once()
    build.assert_called_once_with(factory)
    use_case.execute.assert_called_once()
    command = use_case.execute.call_args.args[0]
    assert command.spec.repository == REPOSITORY
    assert command.spec.context_paths == ("AGENTS.md",)
    factory_context.__exit__.assert_called_once()
    legacy.assert_not_called()


def test_composition_injects_same_scoped_session(monkeypatch):
    factory = Mock()
    session_scope = factory.scoped_session.return_value = MagicMock()
    session = session_scope.__enter__.return_value
    unit_type = Mock()
    repo_type = Mock()
    use_case_type = Mock()
    monkeypatch.setattr(task_deps, "SqlAlchemyUnitOfWork", unit_type)
    monkeypatch.setattr(task_deps, "SqlAlchemyTaskAuthorizationRepository", repo_type)
    monkeypatch.setattr(task_deps, "AuthorizeTaskUseCase", use_case_type)

    with task_deps.create_authorize_task_use_case(factory) as use_case:
        unit_type.assert_called_once_with(session)
        repo_type.assert_called_once_with(session)
        use_case_type.assert_called_once_with(
            unit_type.return_value, repo_type.return_value
        )
        assert use_case is use_case_type.return_value
    session_scope.__exit__.assert_called_once()
