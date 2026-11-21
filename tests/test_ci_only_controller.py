"""V1-03C: CI-only entry point cannot dispatch agents or require worker PEM."""

import json
import sys
from argparse import Namespace
from unittest.mock import Mock

import pytest

from agent_workflow.app_config import AppConfig
from agent_workflow.application.exceptions import ApplicationError
from agent_workflow.composition import postgres_ci_entrypoint
from agent_workflow.integrations.github import WorkflowError
from agent_workflow.orchestration import controller


def test_ci_only_mode_never_loads_coder_runtime(monkeypatch):
    calls = []
    monkeypatch.setattr(
        controller,
        "_ci_operator",
        lambda config, *, watch: calls.append((config, watch)),
    )
    controller.operate({"database": "unused"}, Namespace(ci_once=True, ci_watch=False))
    assert calls == [({"database": "unused"}, False)]


def test_ci_watch_mode_is_separate_from_dev_dispatch(monkeypatch):
    calls = []
    monkeypatch.setattr(
        controller, "_ci_operator", lambda config, *, watch: calls.append(watch)
    )
    controller.operate({"database": "unused"}, Namespace(ci_once=False, ci_watch=True))
    assert calls == [True]


@pytest.mark.parametrize("mode", ["--ci-once", "--ci-watch"])
def test_explicit_pg_routes_before_sqlite_or_coder_runtime(monkeypatch, tmp_path, mode):
    config_file = tmp_path / "controller.json"
    config_file.write_text(json.dumps({"controller_github_app": {}}))
    monkeypatch.setenv(
        "PG_RUNTIME_DSN", "postgresql+psycopg://user:pass@127.0.0.1/workflow_ci"
    )
    monkeypatch.setattr(sys, "argv", ["controller", str(config_file), mode])
    monkeypatch.setattr(
        controller, "process_lock", lambda *_: pytest.fail("SQLite lock")
    )
    monkeypatch.setattr(controller, "operate", lambda *_: pytest.fail("SQLite route"))
    calls = []
    monkeypatch.setattr(
        postgres_ci_entrypoint,
        "run_postgres_ci_once" if mode == "--ci-once" else "run_postgres_ci_watch",
        lambda config, app_config: calls.append(app_config.pg_runtime_dsn),
    )

    controller.main()
    assert len(calls) == 1
    assert calls[0].endswith("/workflow_ci")


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
def test_pg_unsupported_modes_fail_before_sqlite(monkeypatch, tmp_path, flags):
    config_file = tmp_path / "controller.json"
    config_file.write_text("{}")
    monkeypatch.setenv(
        "PG_RUNTIME_DSN", "postgresql+psycopg://user:pass@127.0.0.1/workflow_ci"
    )
    monkeypatch.setattr(sys, "argv", ["controller", str(config_file), *flags])
    monkeypatch.setattr(
        controller, "process_lock", lambda *_: pytest.fail("SQLite lock")
    )
    with pytest.raises(WorkflowError, match="PostgreSQL supports only"):
        controller.main()


def test_invalid_explicit_dsn_never_falls_back(monkeypatch, tmp_path):
    config_file = tmp_path / "controller.json"
    config_file.write_text("{}")
    monkeypatch.setenv("PG_RUNTIME_DSN", "")
    monkeypatch.setattr(sys, "argv", ["controller", str(config_file), "--ci-once"])
    monkeypatch.setattr(
        controller, "process_lock", lambda *_: pytest.fail("SQLite lock")
    )
    with pytest.raises(ValueError, match="PG_RUNTIME_DSN"):
        controller.main()


def test_pg_entrypoint_closes_factory_and_refuses_denied_leader(monkeypatch):
    factory = Mock()
    factory.__enter__ = Mock(return_value=factory)
    factory.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(
        postgres_ci_entrypoint.PostgresFactory, "open", lambda _: factory
    )
    watcher = object()
    monkeypatch.setattr(
        postgres_ci_entrypoint,
        "compose_postgres_controller",
        lambda _: Namespace(ci_watcher=watcher),
    )
    runner = Mock(return_value=None)
    monkeypatch.setattr(postgres_ci_entrypoint, "run_ci_watcher_once", runner)
    checks = Mock(installation_id=17)
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

    with pytest.raises(ApplicationError, match="leadership unavailable"):
        postgres_ci_entrypoint.run_postgres_ci_once(config, app_config, checks=checks)
    runner.assert_called_once_with(watcher, checks, installation_id=17, startup=True)
    checks.get_pull_request.assert_not_called()
    factory.__exit__.assert_called_once()


def test_pg_entrypoint_propagates_failure_and_closes_factory(monkeypatch):
    factory = Mock()
    factory.__enter__ = Mock(return_value=factory)
    factory.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(
        postgres_ci_entrypoint.PostgresFactory, "open", lambda _: factory
    )
    monkeypatch.setattr(
        postgres_ci_entrypoint,
        "compose_postgres_controller",
        lambda _: Namespace(ci_watcher=object()),
    )
    monkeypatch.setattr(
        postgres_ci_entrypoint,
        "run_ci_watcher_once",
        Mock(side_effect=RuntimeError("failed")),
    )
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

    with pytest.raises(RuntimeError, match="failed"):
        postgres_ci_entrypoint.run_postgres_ci_once(config, app_config, checks=Mock())
    factory.__exit__.assert_called_once()


def _watch_setup(monkeypatch):
    factory = Mock()
    factory.__enter__ = Mock(return_value=factory)
    factory.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(
        postgres_ci_entrypoint.PostgresFactory, "open", lambda _: factory
    )
    watchers = []

    def compose(_):
        watcher = object()
        watchers.append(watcher)
        return Namespace(ci_watcher=watcher)

    monkeypatch.setattr(
        postgres_ci_entrypoint,
        "compose_postgres_controller",
        compose,
    )
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
    return factory, watchers, config, app_config


def test_pg_watch_startup_polling_backoff_and_shutdown(monkeypatch):
    factory, watchers, config, app_config = _watch_setup(monkeypatch)
    verdicts = ["BLOCKED"] * 7 + ["SUCCESS", "SUCCESS"]
    runner = Mock(side_effect=[(Namespace(verdict=verdict),) for verdict in verdicts])
    monkeypatch.setattr(postgres_ci_entrypoint, "run_ci_watcher_once", runner)
    sleeps = []
    checks = Mock()

    postgres_ci_entrypoint.run_postgres_ci_watch(
        config,
        app_config,
        checks=checks,
        sleep=sleeps.append,
        stop=lambda: runner.call_count == len(verdicts),
    )

    assert [call.kwargs["startup"] for call in runner.call_args_list] == [
        True,
        *([False] * 8),
    ]
    assert len(watchers) == len(verdicts)
    assert len({id(watcher) for watcher in watchers}) == len(verdicts)
    assert all(
        call.args == (watcher, checks) and call.kwargs["installation_id"] == 17
        for call, watcher in zip(runner.call_args_list, watchers, strict=True)
    )
    assert sleeps == [4, 8, 16, 32, 60, 60, 60, 2]
    factory.__enter__.assert_called_once()
    factory.__exit__.assert_called_once()


@pytest.mark.parametrize("failure", [None, RuntimeError("lost leader")])
def test_pg_watch_denied_or_lost_leader_stops_without_retry(monkeypatch, failure):
    factory, _, config, app_config = _watch_setup(monkeypatch)
    runner = Mock(side_effect=[(Namespace(verdict="SUCCESS"),), failure])
    monkeypatch.setattr(postgres_ci_entrypoint, "run_ci_watcher_once", runner)
    checks = Mock()
    sleeps = []

    with pytest.raises((WorkflowError, RuntimeError)):
        postgres_ci_entrypoint.run_postgres_ci_watch(
            config, app_config, checks=checks, sleep=sleeps.append
        )

    assert runner.call_count == 2
    assert sleeps == [2]
    checks.get_pull_request.assert_not_called()
    factory.__exit__.assert_called_once()


def test_pg_watch_failure_and_interrupt_close_factory(monkeypatch):
    factory, _, config, app_config = _watch_setup(monkeypatch)
    runner = Mock(side_effect=OSError("database unavailable"))
    monkeypatch.setattr(postgres_ci_entrypoint, "run_ci_watcher_once", runner)
    with pytest.raises(OSError, match="database unavailable"):
        postgres_ci_entrypoint.run_postgres_ci_watch(
            config, app_config, checks=Mock(), sleep=Mock()
        )
    runner.assert_called_once()
    factory.__exit__.assert_called_once()

    factory.__exit__.reset_mock()
    runner.reset_mock(side_effect=True)
    runner.return_value = ()
    with pytest.raises(KeyboardInterrupt):
        postgres_ci_entrypoint.run_postgres_ci_watch(
            config,
            app_config,
            checks=Mock(),
            sleep=Mock(side_effect=KeyboardInterrupt),
        )
    runner.assert_called_once()
    factory.__exit__.assert_called_once()
