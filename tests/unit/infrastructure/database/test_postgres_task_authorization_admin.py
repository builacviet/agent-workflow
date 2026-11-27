"""Verify the explicit local admin entrypoint and PostgreSQL factory lifetime."""

import json
import sys
from collections.abc import Iterator
from typing import get_type_hints
from unittest.mock import MagicMock, Mock

import pytest
from pydantic import ValidationError

from agent_workflow.application.admin.use_cases import AdminSeedUseCase
from agent_workflow.application.exceptions import (
    CorruptedPersistenceDataError,
    PersistenceUnavailableError,
)
from agent_workflow.composition import task_deps
from agent_workflow.domain.exceptions import DomainError
from agent_workflow.domain.workflow_identity import REPOSITORY
from agent_workflow.interfaces.cli import admin
from agent_workflow.interfaces.cli.task_spec import (
    ProvisionTaskSpec,
    format_task_spec_error,
)
from agent_workflow.observability.correlation import (
    correlation_scope,
    get_correlation_id,
)

LOCAL_DSN = "postgresql+psycopg://test:disposable@127.0.0.1/fixture"
SECRET = "PRIVATE_SENTINEL"
UNSAFE_ERROR = (
    f"postgresql://user:{SECRET}@localhost/db password={SECRET} sql_params={SECRET}"
)


@pytest.fixture
def spec_file(tmp_path):
    path = tmp_path / "spec.json"
    task_spec = {
        "task_id": "LOCAL-TEST",
        "title": "Authorize local test",
        "description": "Example",
        "acceptance_criteria": ["One task", "One event"],
        "definition_of_done": "Observed",
        "repository": REPOSITORY,
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
    open_factory = Mock()
    monkeypatch.setattr(task_deps.PostgresFactory, "open", open_factory)

    def run(action="provision", *, postgres=False):
        argv = ["agent_workflow.interfaces.cli.admin", action, "--file", str(spec_file)]
        if postgres:
            argv.append("--postgres")
        monkeypatch.setattr(sys, "argv", argv)
        admin.main()

    return run, open_factory


def test_deprecated_postgres_help_is_clear(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["agent_workflow.interfaces.cli.admin", "--help"])
    with pytest.raises(SystemExit) as result:
        admin.main()
    assert result.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "deprecated no-op: local admin commands always use PostgreSQL" in help_text


@pytest.mark.parametrize("arguments", [[UNSAFE_ERROR], ["provision", f"--{SECRET}"]])
def test_bad_command_line_arguments_do_not_echo_submitted_values(
    entrypoint, monkeypatch, capsys, arguments
):
    _, open_factory = entrypoint
    monkeypatch.setattr(
        sys, "argv", ["agent_workflow.interfaces.cli.admin", *arguments]
    )
    with pytest.raises(SystemExit) as error:
        admin.main()
    assert error.value.code == 2
    stderr = capsys.readouterr().err
    assert "local admin arguments" in stderr
    assert SECRET not in stderr
    open_factory.assert_not_called()


@pytest.mark.parametrize("postgres", [False, True])
def test_deprecated_postgres_flag_does_not_select_backend_for_provision(
    entrypoint, monkeypatch, postgres
):
    run, open_factory = entrypoint
    monkeypatch.setenv("PG_RUNTIME_DSN", LOCAL_DSN)
    open_factory.return_value = MagicMock()
    build = MagicMock()
    monkeypatch.setattr(task_deps, "create_authorize_task_use_case", build)

    run(postgres=postgres)

    open_factory.assert_called_once()
    build.return_value.__enter__.return_value.execute.assert_called_once()


@pytest.mark.parametrize(
    "action", ["seed-history", "seed-synthetic", "reset-synthetic"]
)
@pytest.mark.parametrize("postgres", [False, True])
def test_deprecated_postgres_flag_is_ignored_for_seed_and_reset(
    entrypoint, monkeypatch, action, postgres
):
    run, _ = entrypoint
    scope = MagicMock()
    monkeypatch.setattr(admin, "admin_seed_scope", scope)

    run(action=action, postgres=postgres)

    scope.assert_called_once_with(action)
    scope.return_value.__enter__.return_value.execute.assert_called_once()


def test_admin_seed_scope_has_typed_use_case_contract():
    annotation = get_type_hints(task_deps.admin_seed_scope.__wrapped__)["return"]
    assert annotation == Iterator[AdminSeedUseCase]


@pytest.mark.parametrize(
    "dsn",
    [None, "", "sqlite:///fixture.db", LOCAL_DSN.replace("127.0.0.1", "db.example")],
)
def test_missing_invalid_or_remote_dsn_fails_closed(
    entrypoint, dsn, monkeypatch, capsys
):
    run, open_factory = entrypoint
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
    open_factory.assert_not_called()


@pytest.mark.parametrize("override", ["PGHOSTADDR", "PGSERVICE"])
def test_libpq_host_overrides_are_rejected(entrypoint, monkeypatch, capsys, override):
    run, open_factory = entrypoint
    monkeypatch.setenv("PG_RUNTIME_DSN", LOCAL_DSN)
    monkeypatch.setenv(override, "unexpected")
    with pytest.raises(SystemExit) as error:
        run()

    assert error.value.code == 2
    assert "PGHOSTADDR and PGSERVICE are unsupported" in capsys.readouterr().err
    open_factory.assert_not_called()


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"synthetic": SECRET}, "synthetic: expected a boolean"),
        ({SECRET: SECRET}, "TaskSpec: unexpected field"),
        ({"repository": SECRET}, "unauthorized repository"),
        ({"repository": "builacviet/techlead-review-poc"}, "unauthorized repository"),
        ({"task_id": SECRET + "\0"}, "task_id: contains unsupported text"),
        (
            {"context_paths": ["good.md", {"password": SECRET}]},
            "context_paths[1]: expected a string",
        ),
    ],
)
def test_bad_transport_or_business_input_does_not_open_database(
    entrypoint, spec_file, updates, message, monkeypatch, capsys
):
    run, open_factory = entrypoint
    monkeypatch.setenv("PG_RUNTIME_DSN", LOCAL_DSN)
    payload = json.loads(spec_file.read_text(encoding="utf-8"))
    spec_file.write_text(json.dumps(payload | updates), encoding="utf-8")

    with pytest.raises(SystemExit) as error:
        run()

    assert error.value.code == 2
    stderr = capsys.readouterr().err
    assert message in stderr
    assert SECRET not in stderr
    assert "input_value" not in stderr
    assert "Traceback" not in stderr
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


def test_safe_validation_formatter_discards_validator_context_and_input():
    error = ValidationError.from_exception_data(
        "TaskSpec",
        [
            {
                "type": "value_error",
                "loc": ("context_paths", 1),
                "input": UNSAFE_ERROR,
                "ctx": {"error": ValueError(UNSAFE_ERROR)},
            }
        ],
    )
    assert (
        format_task_spec_error(error) == "context_paths[1]: contains unsupported text"
    )


def test_cli_reports_malformed_json_without_echoing_contents(
    entrypoint, spec_file, capsys
):
    run, open_factory = entrypoint
    spec_file.write_text(f'{{"{SECRET}":', encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        run()
    assert error.value.code == 2
    stderr = capsys.readouterr().err
    assert "TaskSpec: malformed JSON" in stderr
    assert SECRET not in stderr
    open_factory.assert_not_called()


@pytest.mark.parametrize(
    ("failure", "exit_code", "message"),
    [
        (None, 0, ""),
        (PersistenceUnavailableError, 75, "Persistence is temporarily unavailable"),
        (TimeoutError, 75, "The local operation timed out"),
        (CorruptedPersistenceDataError, 2, "stored workflow data failed validation"),
        (DomainError, 2, "workflow rule rejected the operation"),
        (ValueError, 2, "invalid local operation configuration"),
        (TypeError, None, ""),
        (RuntimeError, None, ""),
    ],
)
def test_provision_passes_validated_command_and_closes_factory(
    entrypoint, monkeypatch, failure, exit_code, message, capsys
):
    run, open_factory = entrypoint
    monkeypatch.setenv("PG_RUNTIME_DSN", LOCAL_DSN)
    monkeypatch.setenv("CORRELATION_ID", "cli-failure-test")
    factory_context = MagicMock()
    factory = factory_context.__enter__.return_value
    open_factory.return_value = factory_context
    build = MagicMock()
    monkeypatch.setattr(task_deps, "create_authorize_task_use_case", build)
    use_case = build.return_value.__enter__.return_value
    if failure is None:
        run()
    else:
        injected = failure(UNSAFE_ERROR)
        use_case.execute.side_effect = injected
        if exit_code is None:
            with pytest.raises(failure) as error:
                run()
            assert error.value is injected
        else:
            with pytest.raises(SystemExit) as error:
                run()
            assert error.value.code == exit_code
            stderr = capsys.readouterr().err
            assert message in stderr
            assert SECRET not in stderr
            assert "Traceback" not in stderr
            if exit_code == 75:
                record, diagnostic = stderr.splitlines()
                payload = json.loads(record)
                assert payload["event"] == "admin_temporary_failure"
                assert payload["level"] == "WARNING"
                assert payload["correlation_id"] == "cli-failure-test"
                assert payload["action"] == "provision"
                assert payload["reason"] == (
                    "timeout" if failure is TimeoutError else "persistence_unavailable"
                )
                assert message in diagnostic

    assert get_correlation_id() is None
    open_factory.assert_called_once()
    build.assert_called_once_with(factory)
    use_case.execute.assert_called_once()
    command = use_case.execute.call_args.args[0]
    assert command.spec.repository == REPOSITORY
    assert command.spec.context_paths == ("AGENTS.md",)
    factory_context.__exit__.assert_called_once()


@pytest.mark.parametrize(
    "invalid_id", ["*/" + SECRET, " " + SECRET, SECRET + "\n", SECRET * 15]
)
def test_cli_regenerates_correlation_and_logs_once_per_call(
    entrypoint, monkeypatch, capsys, invalid_id
):
    run, open_factory = entrypoint
    monkeypatch.setenv("PG_RUNTIME_DSN", LOCAL_DSN)
    monkeypatch.setenv("CORRELATION_ID", invalid_id)
    seen = []

    def refuse(_config):
        seen.append(get_correlation_id())
        raise PersistenceUnavailableError(UNSAFE_ERROR)

    open_factory.side_effect = refuse
    with correlation_scope(correlation_id="outer-scope"):
        for _ in range(2):
            with pytest.raises(SystemExit) as error:
                run()
            assert error.value.code == 75
            assert get_correlation_id() == "outer-scope"
            stderr = capsys.readouterr().err
            line, _ = stderr.splitlines()
            assert json.loads(line)["correlation_id"] == seen[-1]
            assert seen[-1].startswith("cli-")
            assert SECRET not in stderr

    assert seen[0] != seen[1]
    assert get_correlation_id() is None


def test_composition_injects_same_scoped_session(monkeypatch):
    factory = Mock()
    session_scope = factory.get_scoped_session.return_value = MagicMock()
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
            uow=unit_type.return_value, repository=repo_type.return_value
        )
        assert use_case is use_case_type.return_value
    session_scope.__exit__.assert_called_once()
