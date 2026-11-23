"""V1-03C: CI-only entry point cannot dispatch agents or require worker PEM."""

import json
import runpy
import subprocess
import sys
import textwrap
from argparse import Namespace
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_workflow.app_config import AppConfig
from agent_workflow.application.exceptions import ApplicationError
from agent_workflow.composition import postgres_ci_entrypoint
from agent_workflow.integrations.github import WorkflowError
from agent_workflow.interfaces.cli import ci_controller as ci_cli
from agent_workflow.orchestration import controller


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
    monkeypatch.setattr(
        controller, "process_lock", lambda *_: pytest.fail("SQLite lock")
    )
    monkeypatch.setattr(controller, "operate", lambda *_: pytest.fail("SQLite route"))

    with pytest.raises(SystemExit) as error:
        controller.main()
    assert error.value.code == 2


@pytest.mark.parametrize("flag", ["ci_once", "ci_watch"])
def test_legacy_operate_refuses_ci_only_modes(flag):
    with pytest.raises(WorkflowError, match="require the PostgreSQL CLI entrypoint"):
        controller.operate({"database": "unused"}, Namespace(**{flag: True}))


@pytest.mark.parametrize("mode", ["--ci-once", "--ci-watch"])
@pytest.mark.parametrize("failure", [None, PermissionError("leadership lost")])
@pytest.mark.parametrize("pg_only", [False, True])
def test_explicit_pg_routes_before_sqlite_or_coder_runtime(
    monkeypatch, tmp_path, mode, failure, pg_only
):
    config_file = tmp_path / "controller.json"
    config = {"controller_github_app": {}}
    config_file.write_text(json.dumps(config))
    dsn = "postgresql+psycopg://user:pass@127.0.0.1/workflow_ci"
    monkeypatch.setenv("PG_RUNTIME_DSN", dsn)
    monkeypatch.setattr(sys, "argv", ["controller", str(config_file), mode])
    monkeypatch.setattr(
        controller, "process_lock", lambda *_: pytest.fail("SQLite lock")
    )
    monkeypatch.setattr(controller, "operate", lambda *_: pytest.fail("SQLite route"))
    calls = []
    reconcile = Mock()

    @contextmanager
    def compose_ci_execution(actual_config, app_config):
        assert actual_config == config
        assert app_config.pg_runtime_dsn == dsn
        calls.append("enter")
        try:
            yield reconcile
        finally:
            calls.append("exit")

    def run_handler(handler_name, operation):
        assert operation is reconcile
        calls.append(handler_name)
        if failure is not None:
            raise failure

    monkeypatch.setattr(
        postgres_ci_entrypoint, "compose_ci_execution", compose_ci_execution
    )
    monkeypatch.setattr(
        ci_cli,
        "_execute_ci_once",
        lambda operation: (
            run_handler("once", operation)
            or ci_cli.CiRunResponse(observed_count=0, blocked_count=0)
        ),
    )
    monkeypatch.setattr(
        ci_cli, "run_ci_watch", lambda operation: run_handler("watch", operation)
    )

    def launch():
        if pg_only:
            runpy.run_module(
                "agent_workflow.bootstrap.controller_entrypoint", run_name="__main__"
            )
        else:
            controller.main()

    if failure is None:
        launch()
    else:
        with pytest.raises(SystemExit) as error:
            launch()
        assert error.value.code == 1

    assert calls == ["enter", "once" if mode == "--ci-once" else "watch", "exit"]


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
    # Other tests import the legacy Controller, so use a fresh interpreter.
    script = textwrap.dedent("""
        import runpy
        import sys
        from contextlib import contextmanager
        from functools import partial
        from importlib.abc import MetaPathFinder

        blocked = {
            "agent_workflow.composition.postgres_dispatch_entrypoint",
            "agent_workflow.composition.postgres_dispatch_deps",
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

        from agent_workflow.composition import postgres_ci_entrypoint
        from agent_workflow.interfaces.cli import ci_controller

        requests = []

        def reconcile(request):
            requests.append(request)
            return ci_controller.CiRunResponse(observed_count=0, blocked_count=0)

        @contextmanager
        def compose(config, app_config):
            assert config == {}
            yield reconcile

        postgres_ci_entrypoint.compose_ci_execution = compose
        ci_controller.run_ci_watch = partial(
            ci_controller.run_ci_watch, stop=lambda: bool(requests)
        )
        runpy.run_module(
            "agent_workflow.bootstrap.controller_entrypoint", run_name="__main__"
        )
        assert requests == [ci_controller.CiRunRequest(startup=True)]
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
    monkeypatch.setattr(
        controller, "process_lock", lambda *_: pytest.fail("SQLite lock")
    )
    with pytest.raises(SystemExit) as error:
        controller.main()
    if "--ci-once" in flags or "--ci-watch" in flags:
        assert error.value.code == 2
    else:
        assert error.value.code == 1
        assert "PostgreSQL supports only" in capsys.readouterr().err


@pytest.mark.parametrize("mode", ["--ci-once", "--ci-watch"])
def test_invalid_explicit_dsn_never_falls_back(monkeypatch, tmp_path, mode):
    config_file = tmp_path / "controller.json"
    config_file.write_text("{}")
    monkeypatch.setenv("PG_RUNTIME_DSN", "")
    monkeypatch.setattr(sys, "argv", ["controller", str(config_file), mode])
    monkeypatch.setattr(
        controller, "process_lock", lambda *_: pytest.fail("SQLite lock")
    )
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
    monkeypatch.setattr(
        controller, "process_lock", lambda *_: pytest.fail("SQLite lock")
    )
    monkeypatch.setattr(controller, "operate", lambda *_: pytest.fail("SQLite route"))
    open_database = Mock(side_effect=AssertionError("database must not open"))
    monkeypatch.setattr(postgres_ci_entrypoint.PostgresFactory, "open", open_database)

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
    monkeypatch.setattr(
        postgres_ci_entrypoint.PostgresFactory, "open", lambda _: factory
    )
    use_case = Mock()
    compose = Mock(return_value=use_case)
    monkeypatch.setattr(postgres_ci_entrypoint, "create_ci_reconcile_use_case", compose)
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
        postgres_ci_entrypoint.compose_ci_execution(
            config, app_config, checks=checks
        ) as reconcile,
    ):
        ci_cli.run_ci_once(reconcile)

    compose.assert_called_once_with(factory, checks, installation_id=17)
    use_case.execute.assert_called_once_with(startup=True)
    checks.get_pull_request.assert_not_called()
    factory.__exit__.assert_called_once()


def test_pg_entrypoint_propagates_failure_and_closes_factory(monkeypatch):
    factory, use_case, compose, config, app_config, checks = _watch_setup(monkeypatch)
    use_case.execute.side_effect = RuntimeError("failed")

    with (
        pytest.raises(RuntimeError, match="failed"),
        postgres_ci_entrypoint.compose_ci_execution(
            config, app_config, checks=checks
        ) as reconcile,
    ):
        ci_cli.run_ci_once(reconcile)

    compose.assert_called_once()
    use_case.execute.assert_called_once_with(startup=True)
    factory.__exit__.assert_called_once()


def test_pg_watch_startup_polling_backoff_and_shutdown(monkeypatch):
    factory, use_case, compose, config, app_config, checks = _watch_setup(monkeypatch)
    results = [_evidence(1, 1)] * 7 + [_evidence(1, 0)] * 2
    use_case.execute.side_effect = results
    sleeps = []

    with postgres_ci_entrypoint.compose_ci_execution(
        config, app_config, checks=checks
    ) as reconcile:
        ci_cli.run_ci_watch(
            reconcile,
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
        postgres_ci_entrypoint.compose_ci_execution(
            config, app_config, checks=checks
        ) as reconcile,
    ):
        ci_cli.run_ci_watch(reconcile, sleep=sleeps.append)

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
        postgres_ci_entrypoint.compose_ci_execution(
            config, app_config, checks=checks
        ) as reconcile,
    ):
        ci_cli.run_ci_watch(reconcile, sleep=Mock())
    use_case.execute.assert_called_once()
    compose.assert_called_once()
    factory.__exit__.assert_called_once()

    factory.__exit__.reset_mock()
    use_case.execute.reset_mock(side_effect=True)
    use_case.execute.return_value = _evidence(0, 0)
    with (
        pytest.raises(KeyboardInterrupt),
        postgres_ci_entrypoint.compose_ci_execution(
            config, app_config, checks=checks
        ) as reconcile,
    ):
        ci_cli.run_ci_watch(reconcile, sleep=Mock(side_effect=KeyboardInterrupt))
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

    with postgres_ci_entrypoint.compose_ci_execution(
        config, app_config, checks=checks
    ) as execute:
        first = execute(CiRunRequest(startup=True))
        second = execute(CiRunRequest(startup=False))

    assert first == CiRunResponse(observed_count=3, blocked_count=1)
    assert second == CiRunResponse(observed_count=0, blocked_count=0)
    compose.assert_called_once_with(factory, checks, installation_id=17)
    assert [c.kwargs for c in use_case.execute.call_args_list] == [
        {"startup": True},
        {"startup": False},
    ]
    factory.__exit__.assert_called_once()
