"""CI Controller presentation transports typed request/response objects."""

import json
import logging
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_workflow.app_config import AppConfig
from agent_workflow.application.exceptions import (
    ApplicationError,
    CorruptedPersistenceDataError,
    InvalidApplicationRequestError,
    PersistenceError,
    PersistenceUnavailableError,
)
from agent_workflow.domain.exceptions import DomainError
from agent_workflow.infrastructure.logging.json_formatter import ci_log_scope
from agent_workflow.interfaces.cli import ci_controller
from agent_workflow.observability.correlation import get_correlation_id


def test_one_shot_passes_typed_request_and_renders_typed_response(capsys):
    use_case = Mock()
    use_case.execute.return_value = (SimpleNamespace(verdict="SUCCESS"),)
    ci_controller.run_ci_once(use_case)
    use_case.execute.assert_called_once_with(startup=True)
    assert capsys.readouterr().out == "CI reconciliation: 1 tracked observations\n"


def test_one_shot_error_is_not_disguised_as_success(capsys):
    use_case = Mock()
    use_case.execute.side_effect = PermissionError("leadership lost")
    with pytest.raises(PermissionError, match="leadership lost"):
        ci_controller.run_ci_once(use_case)
    assert capsys.readouterr().out == ""


def test_watch_bounded_backoff_and_startup_semantics():
    blocked = [1] * 7 + [0, 0]
    use_case = Mock()
    use_case.execute.side_effect = [
        (SimpleNamespace(verdict="BLOCKED" if n else "SUCCESS"),) for n in blocked
    ]
    delays = []
    ci_controller.run_ci_watch(
        use_case,
        sleep=delays.append,
        stop=lambda: use_case.execute.call_count == len(blocked),
    )
    assert [c.kwargs for c in use_case.execute.call_args_list] == [
        {"startup": True},
        *[{"startup": False}] * 8,
    ]
    assert delays == [4, 8, 16, 32, 60, 60, 60, 2]


@pytest.mark.parametrize(
    "failure",
    [
        PermissionError("leadership lost"),
        OSError("database unavailable"),
        KeyboardInterrupt(),
    ],
)
def test_watch_does_not_consume_runtime_errors(failure):
    use_case = Mock()
    use_case.execute.side_effect = failure
    with pytest.raises(type(failure)):
        ci_controller.run_ci_watch(use_case, sleep=Mock())
    use_case.execute.assert_called_once_with(startup=True)


@pytest.mark.parametrize(
    ("verdicts", "expected"),
    [
        ((), ci_controller.CiRunResponse(0, 0)),
        (("SUCCESS",), ci_controller.CiRunResponse(1, 0)),
        (("FAILURE",), ci_controller.CiRunResponse(1, 0)),
        (("SUCCESS", "BLOCKED", "FAILURE"), ci_controller.CiRunResponse(3, 1)),
    ],
)
def test_controller_maps_completed_evidence_to_typed_response(verdicts, expected):
    use_case = Mock()
    use_case.execute.return_value = tuple(
        SimpleNamespace(verdict=verdict) for verdict in verdicts
    )
    request = ci_controller.CiRunRequest(startup=True)
    response = ci_controller.run_ci_pass(use_case, request)
    assert response == expected
    use_case.execute.assert_called_once_with(startup=True)


@pytest.mark.parametrize(
    ("failure", "exit_code", "reason"),
    [
        (DomainError("dsn=secret"), 2, "request_rejected"),
        (InvalidApplicationRequestError("pem=/secret/key"), 2, "request_rejected"),
        (
            PersistenceUnavailableError("password=secret"),
            75,
            "persistence_unavailable",
        ),
        (TimeoutError("sql parameters secret"), 75, "timeout"),
        (ValueError("runtime value; dsn=secret"), 1, "unexpected_failure"),
        (
            CorruptedPersistenceDataError("postgresql://secret"),
            1,
            "persistence_data_invalid",
        ),
        (
            ApplicationError("leadership denied; dsn=secret"),
            1,
            "application_failure",
        ),
        (
            PersistenceError("ordinary persistence contract failure"),
            1,
            "application_failure",
        ),
        (
            PermissionError("leadership lost; pem=/secret"),
            1,
            "permission_failure",
        ),
        (
            PermissionError("file access denied; pem=/secret"),
            1,
            "permission_failure",
        ),
        (
            OSError("https://user:secret@example.invalid"),
            1,
            "integration_failure",
        ),
        (RuntimeError("sql params=secret"), 1, "unexpected_failure"),
    ],
)
def test_terminal_boundary_maps_errors_safely_and_resets_scope(
    tmp_path, monkeypatch, capsys, failure, exit_code, reason
):
    config_file = tmp_path / "controller.json"
    config_file.write_text("{}", encoding="utf-8")
    monkeypatch.setenv(
        "PG_RUNTIME_DSN", "postgresql+psycopg://user:pass@127.0.0.1/workflow_ci"
    )
    monkeypatch.setenv("CORRELATION_ID", "ci-test-42")

    def fail_with_correlation(*, startup):
        assert startup is True
        assert get_correlation_id() == "ci-test-42"
        raise failure

    use_case = Mock()
    use_case.execute.side_effect = fail_with_correlation

    @contextmanager
    def compose(config, app_config):
        assert config == {}
        assert app_config.pg_runtime_dsn.startswith("postgresql+psycopg://")
        yield use_case

    logger = logging.getLogger("agent_workflow.interfaces.cli.ci_controller")
    original = list(logger.handlers), logger.level, logger.propagate
    with pytest.raises(SystemExit) as error:
        ci_controller.run_ci_command(
            ci_once=True,
            ci_watch=False,
            dispatch_once=False,
            acknowledge=None,
            reconciled=False,
            prepare=lambda: ({}, AppConfig.from_env()),
            compose=compose,
            log_scope=ci_log_scope,
        )

    assert error.value.code == exit_code
    captured = capsys.readouterr()
    assert "secret" not in captured.err
    assert "secret" not in captured.out
    lines = captured.err.splitlines()
    assert len(lines) == 2
    log = json.loads(lines[0])
    assert log["event"] == "ci_cli_failure"
    assert log["reason"] == reason
    assert log["mode"] == "once"
    assert log["correlation_id"] == "ci-test-42"
    assert get_correlation_id() is None
    assert (logger.handlers, logger.level, logger.propagate) == original
    use_case.execute.assert_called_once_with(startup=True)


def test_terminal_boundary_handles_watch_interrupt_without_retry(
    tmp_path, monkeypatch, capsys
):
    config_file = tmp_path / "controller.json"
    config_file.write_text("{}", encoding="utf-8")
    monkeypatch.setenv(
        "PG_RUNTIME_DSN", "postgresql+psycopg://user:pass@127.0.0.1/workflow_ci"
    )
    use_case = Mock()
    use_case.execute.side_effect = KeyboardInterrupt()

    @contextmanager
    def compose(config, app_config):
        yield use_case

    with pytest.raises(SystemExit) as error:
        ci_controller.run_ci_command(
            ci_once=False,
            ci_watch=True,
            dispatch_once=False,
            acknowledge=None,
            reconciled=False,
            prepare=lambda: ({}, AppConfig.from_env()),
            compose=compose,
            log_scope=ci_log_scope,
        )

    assert error.value.code == 130
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "CI watch interrupted" in captured.err
    use_case.execute.assert_called_once_with(startup=True)


def test_terminal_boundary_does_not_report_success_when_composition_close_fails(
    monkeypatch, capsys
):
    monkeypatch.setenv(
        "PG_RUNTIME_DSN", "postgresql+psycopg://user:pass@127.0.0.1/workflow_ci"
    )

    @contextmanager
    def compose(config, app_config):
        yield SimpleNamespace(
            execute=Mock(return_value=(SimpleNamespace(verdict="SUCCESS"),))
        )
        raise OSError("dsn=close-secret")

    with pytest.raises(SystemExit) as error:
        ci_controller.run_ci_command(
            ci_once=True,
            ci_watch=False,
            dispatch_once=False,
            acknowledge=None,
            reconciled=False,
            prepare=lambda: ({}, AppConfig.from_env()),
            compose=compose,
            log_scope=ci_log_scope,
        )

    captured = capsys.readouterr()
    assert error.value.code == 1
    assert captured.out == ""
    assert "close-secret" not in captured.err


def test_terminal_boundary_reports_one_shot_success_after_composition_closes(
    monkeypatch,
):
    monkeypatch.setenv(
        "PG_RUNTIME_DSN", "postgresql+psycopg://user:pass@127.0.0.1/workflow_ci"
    )
    events = []

    @contextmanager
    def compose(config, app_config):
        events.append("opened")
        yield SimpleNamespace(
            execute=Mock(return_value=(SimpleNamespace(verdict="SUCCESS"),))
        )
        events.append("closed")

    monkeypatch.setattr(
        ci_controller, "_print_ci_once", lambda _result: events.append("reported")
    )
    ci_controller.run_ci_command(
        ci_once=True,
        ci_watch=False,
        dispatch_once=False,
        acknowledge=None,
        reconciled=False,
        prepare=lambda: ({}, AppConfig.from_env()),
        compose=compose,
        log_scope=ci_log_scope,
    )

    assert events == ["opened", "closed", "reported"]


def test_terminal_boundary_rejects_invalid_configuration_safely(
    tmp_path, monkeypatch, capsys
):
    config_file = tmp_path / "dsn=super-secret.json"
    config_file.write_text("not-json-password=super-secret", encoding="utf-8")
    monkeypatch.setenv(
        "PG_RUNTIME_DSN", "postgresql+psycopg://user:pass@127.0.0.1/workflow_ci"
    )
    compose = Mock(side_effect=AssertionError("must not compose"))

    with pytest.raises(SystemExit) as error:
        ci_controller.run_ci_command(
            ci_once=True,
            ci_watch=False,
            dispatch_once=False,
            acknowledge=None,
            reconciled=False,
            prepare=lambda: (
                json.loads(
                    config_file.resolve(strict=True).read_text(encoding="utf-8")
                ),
                AppConfig.from_env(),
            ),
            compose=compose,
            log_scope=ci_log_scope,
        )

    assert error.value.code == 2
    stderr = capsys.readouterr().err
    assert "super-secret" not in stderr
    compose.assert_not_called()


def test_terminal_boundary_maps_compose_entry_valueerror_as_configuration(
    monkeypatch, capsys
):
    monkeypatch.setenv(
        "PG_RUNTIME_DSN", "postgresql+psycopg://user:pass@127.0.0.1/workflow_ci"
    )

    class InvalidComposition:
        def __enter__(self):
            raise ValueError("pem=/private/controller.key")

        def __exit__(self, *_args):
            pytest.fail("failed composition entry must not be exited")

    def compose(_config, _app_config):
        return InvalidComposition()

    with pytest.raises(SystemExit) as error:
        ci_controller.run_ci_command(
            ci_once=True,
            ci_watch=False,
            dispatch_once=False,
            acknowledge=None,
            reconciled=False,
            prepare=lambda: ({}, AppConfig.from_env()),
            compose=compose,
            log_scope=ci_log_scope,
        )

    captured = capsys.readouterr()
    assert error.value.code == 2
    assert "controller.key" not in captured.err
    assert "pem=" not in captured.err
