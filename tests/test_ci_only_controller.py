"""V1-03C: CI-only entry point cannot dispatch agents or require worker PEM."""

import json
import subprocess
import sys
import textwrap
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_workflow.app_config import AppConfig
from agent_workflow.application.exceptions import ApplicationError
from agent_workflow.composition import ci_deps
from agent_workflow.interfaces.cli import ci_controller as ci_cli
from agent_workflow.interfaces.cli import controller
from agent_workflow.interfaces.cli.controller_cli import run_controller_cli


def _evidence(observed_count, blocked_count):
    return tuple(
        SimpleNamespace(verdict="BLOCKED" if index < blocked_count else "SUCCESS")
        for index in range(observed_count)
    )


@pytest.mark.parametrize("mode", ["--ci-once", "--ci-watch"])
def test_ci_mode_requires_postgres_before_any_fallback(monkeypatch, tmp_path, mode):
    config_file = tmp_path / "controller.json"
    config_file.write_text("{}")
    monkeypatch.delenv("PG_RUNTIME_DSN", raising=False)
    monkeypatch.setattr(sys, "argv", ["controller", str(config_file), mode])

    with pytest.raises(SystemExit) as error:
        controller.main()
    assert error.value.code == 2


@pytest.mark.parametrize("mode", ["--ci-once", "--ci-watch"])
@pytest.mark.parametrize("failure", [None, PermissionError("leadership lost")])
def test_explicit_pg_routes_before_sqlite_or_coder_runtime(
    monkeypatch, tmp_path, mode, failure
):
    config_file = tmp_path / "controller.json"
    config = {"controller_github_app": {}}
    config_file.write_text(json.dumps(config))
    dsn = "postgresql+psycopg://user:pass@127.0.0.1/workflow_ci"
    monkeypatch.setenv("PG_RUNTIME_DSN", dsn)
    monkeypatch.setattr(sys, "argv", ["controller", str(config_file), mode])
    calls = []
    use_case = Mock()

    @contextmanager
    def compose_ci_execution(actual_config, app_config):
        assert actual_config == config
        assert app_config.pg_runtime_dsn == dsn
        calls.append("enter")
        try:
            yield use_case
        finally:
            calls.append("exit")

    def run_handler(handler_name, operation, request=None):
        assert operation is use_case
        if handler_name == "once":
            assert request == ci_cli.CiRunRequest(startup=True)
        calls.append(handler_name)
        if failure is not None:
            raise failure

    monkeypatch.setattr(ci_deps, "compose_ci_execution", compose_ci_execution)
    monkeypatch.setattr(
        ci_cli,
        "run_ci_pass",
        lambda operation, request: (
            run_handler("once", operation, request)
            or ci_cli.CiRunResponse(observed_count=0, blocked_count=0)
        ),
    )
    monkeypatch.setattr(
        ci_cli, "run_ci_watch", lambda operation: run_handler("watch", operation)
    )

    if failure is None:
        controller.main()
    else:
        with pytest.raises(SystemExit) as error:
            controller.main()
        assert error.value.code == 1

    assert calls == ["enter", "once" if mode == "--ci-once" else "watch", "exit"]


def test_pg_ci_cli_uses_injected_app_config(monkeypatch, tmp_path, capsys):
    config = tmp_path / "controller-ci.json"
    config.write_text("{}")
    monkeypatch.setenv(
        "PG_RUNTIME_DSN", "postgresql+psycopg://user:pass@127.0.0.1/unused"
    )
    monkeypatch.setattr(sys, "argv", ["controller", str(config), "--ci-once"])
    monkeypatch.setattr(
        AppConfig, "from_env", Mock(side_effect=AssertionError("read process settings"))
    )
    settings = AppConfig(pg_runtime_dsn="postgresql+psycopg://user:pass@127.0.0.1/ci")
    load_app_config = Mock(return_value=settings)
    use_case = Mock()
    use_case.execute.return_value = ()
    compose_ci = Mock(return_value=nullcontext(use_case))
    compose_dispatch = Mock(side_effect=AssertionError("dispatch selected in CI mode"))

    run_controller_cli(
        compose_ci=compose_ci,
        compose_dispatch=compose_dispatch,
        load_app_config=load_app_config,
        log_scope=nullcontext,
    )

    AppConfig.from_env.assert_not_called()
    load_app_config.assert_called_once_with()
    compose_ci.assert_called_once_with({}, settings)
    compose_dispatch.assert_not_called()
    use_case.execute.assert_called_once_with(startup=True)
    assert capsys.readouterr().out == "CI reconciliation: 0 tracked observations\n"


@pytest.mark.parametrize("mode", ["--ci-once", "--ci-watch"])
def test_pg_ci_launcher_does_not_import_dispatch_provider_or_worker(
    monkeypatch, tmp_path, mode
):
    config = tmp_path / "controller-ci.json"
    config.write_text("{}")
    monkeypatch.setenv(
        "PG_RUNTIME_DSN", "postgresql+psycopg://user:pass@127.0.0.1/workflow_ci"
    )
    monkeypatch.setenv("WFC_SANDBOX_STATE_DIR", str(tmp_path / "fences"))
    # A fresh interpreter exposes every attempted import during CLI startup.
    script = textwrap.dedent("""
        import runpy
        import sqlite3
        import sys
        from contextlib import contextmanager
        from functools import partial
        from importlib.abc import MetaPathFinder
        from types import SimpleNamespace

        blocked = {
            "agent_workflow.composition.dispatch_deps",
            "agent_workflow.composition.worker_deps",
            "agent_workflow.runners.coder_runtime",
            "agent_workflow.runners.sandbox",
            "agent_workflow.storage.dispatch_repository",
        }

        class BlockDispatchImports(MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname in blocked:
                    raise AssertionError(f"CI tried to load {fullname}")

        assert not blocked.intersection(sys.modules)
        sys.meta_path.insert(0, BlockDispatchImports())

        def no_sqlite(*args, **kwargs):
            raise AssertionError("CI tried to connect to SQLite")

        sqlite3.connect = no_sqlite

        from agent_workflow.composition import ci_deps
        from agent_workflow.interfaces.cli import ci_controller

        requests = []

        def execute(*, startup):
            requests.append(startup)
            return ()

        @contextmanager
        def compose(config, app_config):
            assert config == {}
            yield SimpleNamespace(execute=execute)

        ci_deps.compose_ci_execution = compose
        ci_controller.run_ci_watch = partial(
            ci_controller.run_ci_watch, stop=lambda: bool(requests)
        )
        runpy.run_module(
            "agent_workflow.interfaces.cli.controller", run_name="__main__"
        )
        assert requests == [True]
        assert not blocked.intersection(sys.modules)
    """)

    result = subprocess.run(
        [sys.executable, "-c", script, str(config), mode],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "flags",
    [
        [],
        ["--once", "--acknowledge", "x"],
        ["--once", "--reconciled"],
        ["--ci-once", "--ci-watch"],
        ["--ci-watch", "--once"],
        ["--ci-watch", "--acknowledge", "x"],
        ["--ci-watch", "--reconciled"],
        ["--ci-once", "--once"],
        ["--ci-once", "--acknowledge", "x"],
        ["--ci-once", "--reconciled"],
    ],
)
def test_pg_unsupported_modes_fail_before_sqlite(monkeypatch, tmp_path, flags, capsys):
    config_file = tmp_path / "controller.json"
    config_file.write_text("{}")
    monkeypatch.setenv(
        "PG_RUNTIME_DSN", "postgresql+psycopg://user:pass@127.0.0.1/workflow_ci"
    )
    monkeypatch.setattr(sys, "argv", ["controller", str(config_file), *flags])
    with pytest.raises(SystemExit) as error:
        controller.main()
    assert error.value.code == 2
    assert "exactly one supported mode" in capsys.readouterr().err


@pytest.mark.parametrize("mode", ["--ci-once", "--ci-watch"])
def test_invalid_explicit_dsn_never_falls_back(monkeypatch, tmp_path, mode):
    config_file = tmp_path / "controller.json"
    config_file.write_text("{}")
    monkeypatch.setenv("PG_RUNTIME_DSN", "")
    monkeypatch.setattr(sys, "argv", ["controller", str(config_file), mode])
    with pytest.raises(SystemExit) as error:
        controller.main()
    assert error.value.code == 2


@pytest.mark.parametrize(
    ("app_id", "pem_name"),
    [(0, "controller.pem"), (1, "controller-private.key")],
)
def test_invalid_controller_app_config_exits_two_without_database_or_sqlite(
    monkeypatch, tmp_path, capsys, app_id, pem_name
):
    config_file = tmp_path / "controller.json"
    private_pem_path = tmp_path / pem_name
    config_file.write_text(
        json.dumps(
            {
                "controller_github_app": {
                    "app_id": app_id,
                    "installation_id": 17,
                    "pem": str(private_pem_path),
                }
            }
        )
    )
    monkeypatch.setenv(
        "PG_RUNTIME_DSN", "postgresql+psycopg://user:pass@127.0.0.1/workflow_ci"
    )
    monkeypatch.setattr(sys, "argv", ["controller", str(config_file), "--ci-once"])
    open_database = Mock(side_effect=AssertionError("database must not open"))
    monkeypatch.setattr(ci_deps.PostgresFactory, "open", open_database)

    with pytest.raises(SystemExit) as error:
        controller.main()

    stderr = capsys.readouterr().err
    assert error.value.code == 2
    assert str(private_pem_path) not in stderr
    assert "app_id" not in stderr
    open_database.assert_not_called()


def _watch_setup(monkeypatch):
    factory = Mock()
    factory.__enter__ = Mock(return_value=factory)
    factory.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(ci_deps.PostgresFactory, "open", lambda _: factory)
    use_case = Mock()
    compose = Mock(return_value=use_case)
    monkeypatch.setattr(ci_deps, "create_ci_reconcile_use_case", compose)
    config = {
        "controller_github_app": {
            "app_id": 1,
            "installation_id": 17,
            "pem": "controller.pem",
        }
    }
    app_config = AppConfig(
        pg_runtime_dsn="postgresql+psycopg://user:pass@127.0.0.1/workflow_ci"
    )
    checks = Mock(installation_id=17)
    return factory, use_case, compose, config, app_config, checks


def test_pg_entrypoint_closes_factory_and_refuses_denied_leader(monkeypatch):
    factory, use_case, compose, config, app_config, checks = _watch_setup(monkeypatch)
    use_case.execute.side_effect = ApplicationError(
        "PostgreSQL CI watcher leadership unavailable"
    )

    with (
        pytest.raises(ApplicationError, match="leadership unavailable"),
        ci_deps.compose_ci_execution(
            config, app_config, checks=checks
        ) as scoped_use_case,
    ):
        ci_cli.run_ci_once(scoped_use_case)

    compose.assert_called_once_with(factory, checks, installation_id=17)
    use_case.execute.assert_called_once_with(startup=True)
    checks.get_pull_request.assert_not_called()
    factory.__exit__.assert_called_once()


def test_pg_entrypoint_propagates_failure_and_closes_factory(monkeypatch):
    factory, use_case, compose, config, app_config, checks = _watch_setup(monkeypatch)
    use_case.execute.side_effect = RuntimeError("failed")

    with (
        pytest.raises(RuntimeError, match="failed"),
        ci_deps.compose_ci_execution(
            config, app_config, checks=checks
        ) as scoped_use_case,
    ):
        ci_cli.run_ci_once(scoped_use_case)

    compose.assert_called_once()
    use_case.execute.assert_called_once_with(startup=True)
    factory.__exit__.assert_called_once()


def test_pg_watch_startup_polling_backoff_and_shutdown(monkeypatch):
    factory, use_case, compose, config, app_config, checks = _watch_setup(monkeypatch)
    results = [_evidence(1, 1)] * 7 + [_evidence(1, 0)] * 2
    use_case.execute.side_effect = results
    sleeps = []

    with ci_deps.compose_ci_execution(
        config, app_config, checks=checks
    ) as scoped_use_case:
        ci_cli.run_ci_watch(
            scoped_use_case,
            sleep=sleeps.append,
            stop=lambda: use_case.execute.call_count == len(results),
        )

    assert [call.kwargs["startup"] for call in use_case.execute.call_args_list] == [
        True,
        *([False] * 8),
    ]
    compose.assert_called_once_with(factory, checks, installation_id=17)
    assert sleeps == [4, 8, 16, 32, 60, 60, 60, 2]
    factory.__enter__.assert_called_once()
    factory.__exit__.assert_called_once()


@pytest.mark.parametrize(
    "failure", [ApplicationError("leadership unavailable"), PermissionError("lost")]
)
def test_pg_watch_denied_or_lost_leader_stops_without_retry(monkeypatch, failure):
    factory, use_case, compose, config, app_config, checks = _watch_setup(monkeypatch)
    use_case.execute.side_effect = [
        _evidence(1, 0),
        failure,
    ]
    sleeps = []

    with (
        pytest.raises(type(failure)),
        ci_deps.compose_ci_execution(
            config, app_config, checks=checks
        ) as scoped_use_case,
    ):
        ci_cli.run_ci_watch(scoped_use_case, sleep=sleeps.append)

    assert use_case.execute.call_count == 2
    compose.assert_called_once()
    assert sleeps == [2]
    checks.get_pull_request.assert_not_called()
    factory.__exit__.assert_called_once()


def test_pg_watch_failure_and_interrupt_close_factory(monkeypatch):
    factory, use_case, compose, config, app_config, checks = _watch_setup(monkeypatch)
    use_case.execute.side_effect = OSError("database unavailable")
    with (
        pytest.raises(OSError, match="database unavailable"),
        ci_deps.compose_ci_execution(
            config, app_config, checks=checks
        ) as scoped_use_case,
    ):
        ci_cli.run_ci_watch(scoped_use_case, sleep=Mock())
    use_case.execute.assert_called_once()
    compose.assert_called_once()
    factory.__exit__.assert_called_once()

    factory.__exit__.reset_mock()
    use_case.execute.reset_mock(side_effect=True)
    use_case.execute.return_value = _evidence(0, 0)
    with (
        pytest.raises(KeyboardInterrupt),
        ci_deps.compose_ci_execution(
            config, app_config, checks=checks
        ) as scoped_use_case,
    ):
        ci_cli.run_ci_watch(scoped_use_case, sleep=Mock(side_effect=KeyboardInterrupt))
    use_case.execute.assert_called_once()
    factory.__exit__.assert_called_once()


def test_composition_injects_once_and_presentation_maps_response(monkeypatch):
    """No runtime callback may reenter composition or leak a database adapter."""
    from agent_workflow.interfaces.cli.ci_controller import CiRunRequest, CiRunResponse

    factory, use_case, compose, config, app_config, checks = _watch_setup(monkeypatch)
    use_case.execute.side_effect = [
        _evidence(3, 1),
        _evidence(0, 0),
    ]

    with ci_deps.compose_ci_execution(
        config, app_config, checks=checks
    ) as scoped_use_case:
        assert scoped_use_case is use_case
        first = ci_cli.run_ci_pass(scoped_use_case, CiRunRequest(startup=True))
        second = ci_cli.run_ci_pass(scoped_use_case, CiRunRequest(startup=False))

    assert first == CiRunResponse(observed_count=3, blocked_count=1)
    assert second == CiRunResponse(observed_count=0, blocked_count=0)
    compose.assert_called_once_with(factory, checks, installation_id=17)
    assert [c.kwargs for c in use_case.execute.call_args_list] == [
        {"startup": True},
        {"startup": False},
    ]
    factory.__exit__.assert_called_once()
