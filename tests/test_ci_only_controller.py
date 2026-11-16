"""V1-03C: CI-only entry point cannot dispatch agents or require worker PEM."""

import json
import sys
from argparse import Namespace
from unittest.mock import Mock

import pytest

from agent_workflow.app_config import AppConfig
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


def test_explicit_pg_routes_before_sqlite_or_coder_runtime(monkeypatch, tmp_path):
    config_file = tmp_path / "controller.json"
    config_file.write_text(json.dumps({"controller_github_app": {}}))
    monkeypatch.setenv(
        "PG_RUNTIME_DSN", "postgresql+psycopg://user:pass@127.0.0.1/workflow_ci"
    )
    monkeypatch.setattr(sys, "argv", ["controller", str(config_file), "--ci-once"])
    monkeypatch.setattr(
        controller, "process_lock", lambda *_: pytest.fail("SQLite lock")
    )
    monkeypatch.setattr(controller, "operate", lambda *_: pytest.fail("SQLite route"))
    calls = []
    monkeypatch.setattr(
        postgres_ci_entrypoint,
        "run_postgres_ci_once",
        lambda config, app_config: calls.append(app_config.pg_runtime_dsn),
    )

    controller.main()
    assert len(calls) == 1
    assert calls[0].endswith("/workflow_ci")


@pytest.mark.parametrize(
    "flags",
    [
        [],
        ["--ci-watch"],
        ["--once"],
        ["--ci-once", "--ci-watch"],
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
    with pytest.raises(WorkflowError, match="PostgreSQL supports only --ci-once"):
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

    with pytest.raises(WorkflowError, match="leadership unavailable"):
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
