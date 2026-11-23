"""The PostgreSQL command validates, invokes and reports at the CLI boundary."""

import ast
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

import pytest

from agent_workflow.app_config import AppConfig
from agent_workflow.application.exceptions import InvalidApplicationRequestError
from agent_workflow.interfaces.cli import dispatch_controller as cli

DSN = "postgresql+psycopg://user:fake@127.0.0.1/dispatch_test"


@pytest.fixture
def command(monkeypatch, tmp_path):
    monkeypatch.setenv("PG_RUNTIME_DSN", DSN)
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "1")
    monkeypatch.setenv("WFC_SANDBOX_STATE_DIR", str(tmp_path / "fences"))
    return {
        "once": True,
        "acknowledge": None,
        "reconciled": False,
        "prepare": lambda: {"tasks": {"T": {}}, "timeout_seconds": 60},
    }


@pytest.mark.parametrize("completed", [True, False])
def test_command_reports_only_after_resources_close(command, capsys, completed):
    execute = Mock(return_value=completed)

    @contextmanager
    def compose(config, app_config):
        assert app_config.pg_runtime_dsn == DSN
        assert config == {"tasks": {"T": {}}, "timeout_seconds": 60}
        yield execute_use_case
        execute.assert_called_once_with({"T"}, 60)
        assert capsys.readouterr().out == ""

    execute_use_case = Mock(execute=execute)
    cli.run_dispatch_command(**command, compose=compose)
    assert capsys.readouterr() == (
        "PostgreSQL dispatch: Worker completed.\n"
        if completed
        else "PostgreSQL dispatch: no work claimed.\n",
        "",
    )


def test_pass_builds_typed_response_and_preserves_exact_allowlist():
    execute_use_case = Mock()
    execute_use_case.execute.return_value = True

    @contextmanager
    def compose(_config, _app_config):
        yield execute_use_case

    result = cli.run_dispatch_pass(
        {"tasks": {"CaseSensitive": {}}}, AppConfig(pg_runtime_dsn=DSN), compose=compose
    )
    execute_use_case.execute.assert_called_once_with({"CaseSensitive"}, 1800)
    assert result == cli.DispatchRunResponse(worker_completed=True)


@pytest.mark.parametrize(
    "config",
    [
        [],
        {"tasks": []},
        {"tasks": {}},
        {"tasks": {" ": {}}},
        {"tasks": {1: {}}},
        {"tasks": {"T": None}},
        {"tasks": {"T": {}}, "timeout_seconds": True},
        {"tasks": {"T": {}}, "timeout_seconds": "60"},
        {"tasks": {"T": {}}, "timeout_seconds": 59},
        {"tasks": {"T": {}}, "timeout_seconds": 3601},
    ],
)
def test_invalid_request_skips_composition(command, config, capsys):
    compose = Mock()
    with pytest.raises(SystemExit) as failure:
        cli.run_dispatch_command(
            **(command | {"prepare": lambda: config}), compose=compose
        )
    assert failure.value.code == 1
    compose.assert_not_called()
    assert capsys.readouterr() == (
        "",
        "Invalid PostgreSQL dispatch Worker configuration.\n",
    )


@pytest.mark.parametrize(
    "flags", [{"once": False}, {"acknowledge": "secret"}, {"reconciled": True}]
)
def test_invalid_flags_skip_config_and_composition(command, flags, capsys):
    prepare, compose = Mock(), Mock()
    with pytest.raises(SystemExit) as failure:
        cli.run_dispatch_command(
            **(command | flags | {"prepare": prepare}), compose=compose
        )
    assert failure.value.code == 1
    prepare.assert_not_called()
    compose.assert_not_called()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "secret" not in captured.err


@pytest.mark.parametrize("phase", ["prepare", "enter", "execute", "close", "interrupt"])
def test_command_sanitizes_failures_and_omits_success(command, capsys, phase):
    secret = "synthetic secret DSN and untrusted subprocess output"
    execute = Mock(return_value=True)

    if phase == "prepare":
        command["prepare"] = Mock(side_effect=ValueError(secret))
    elif phase == "execute":
        execute.side_effect = RuntimeError(secret)
    elif phase == "interrupt":
        execute.side_effect = KeyboardInterrupt(secret)

    @contextmanager
    def compose(_config, _app_config):
        if phase == "enter":
            raise InvalidApplicationRequestError(secret)
        try:
            yield Mock(execute=execute)
        finally:
            if phase == "close":
                raise OSError(secret)

    with pytest.raises(SystemExit) as failure:
        cli.run_dispatch_command(**command, compose=compose)
    assert failure.value.code == (130 if phase == "interrupt" else 1)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith(
        "Invalid PostgreSQL dispatch" if phase == "enter" else "PostgreSQL dispatch"
    )
    assert secret not in captured.err
    assert "Traceback" not in captured.err


def test_dispatch_adapter_imports_only_cli_and_application_dependencies():
    tree = ast.parse(Path(cli.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(
                alias.name in {"argparse", "json", "sys"} for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0 and node.module
            assert node.module in {
                "collections.abc",
                "contextlib",
                "dataclasses",
                "pathlib",
                "typing",
                "agent_workflow.app_config",
            } or node.module.startswith("agent_workflow.application.")
