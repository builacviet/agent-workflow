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


def _watch_setup(monkeypatch):
    factory = Mock()
    factory.__enter__ = Mock(return_value=factory)
    factory.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(
        postgres_ci_entrypoint.PostgresFactory, "open", lambda _: factory
    )
    use_case = Mock()
    compose = Mock(return_value=use_case)
    monkeypatch.setattr(
        postgres_ci_entrypoint, "create_ci_pass_use_case", compose
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
    checks = Mock(installation_id=17)
    return factory, use_case, compose, config, app_config, checks


def test_pg_entrypoint_closes_factory_and_refuses_denied_leader(monkeypatch):
    factory, use_case, compose, config, app_config, checks = _watch_setup(monkeypatch)
    use_case.execute.side_effect = ApplicationError(
        "PostgreSQL CI watcher leadership unavailable"
    )

    with pytest.raises(ApplicationError, match="leadership unavailable"):
        postgres_ci_entrypoint.run_postgres_ci_once(config, app_config, checks=checks)

    compose.assert_called_once_with(factory, checks, installation_id=17)
    use_case.execute.assert_called_once_with(startup=True)
    checks.get_pull_request.assert_not_called()
    factory.__exit__.assert_called_once()


def test_pg_entrypoint_propagates_failure_and_closes_factory(monkeypatch):
    factory, use_case, compose, config, app_config, checks = _watch_setup(monkeypatch)
    use_case.execute.side_effect = RuntimeError("failed")

    with pytest.raises(RuntimeError, match="failed"):
        postgres_ci_entrypoint.run_postgres_ci_once(config, app_config, checks=checks)

    compose.assert_called_once()
    use_case.execute.assert_called_once_with(startup=True)
    factory.__exit__.assert_called_once()


def test_pg_watch_startup_polling_backoff_and_shutdown(monkeypatch):
    factory, use_case, compose, config, app_config, checks = _watch_setup(monkeypatch)
    verdicts = ["BLOCKED"] * 7 + ["SUCCESS", "SUCCESS"]
    use_case.execute.side_effect = [
        (Namespace(verdict=verdict),) for verdict in verdicts
    ]
    sleeps = []

    postgres_ci_entrypoint.run_postgres_ci_watch(
        config,
        app_config,
        checks=checks,
        sleep=sleeps.append,
        stop=lambda: use_case.execute.call_count == len(verdicts),
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
        (Namespace(verdict="SUCCESS"),),
        failure,
    ]
    sleeps = []

    with pytest.raises(type(failure)):
        postgres_ci_entrypoint.run_postgres_ci_watch(
            config, app_config, checks=checks, sleep=sleeps.append
        )

    assert use_case.execute.call_count == 2
    compose.assert_called_once()
    assert sleeps == [2]
    checks.get_pull_request.assert_not_called()
    factory.__exit__.assert_called_once()


def test_pg_watch_failure_and_interrupt_close_factory(monkeypatch):
    factory, use_case, compose, config, app_config, checks = _watch_setup(monkeypatch)
    use_case.execute.side_effect = OSError("database unavailable")
    with pytest.raises(OSError, match="database unavailable"):
        postgres_ci_entrypoint.run_postgres_ci_watch(
            config, app_config, checks=checks, sleep=Mock()
        )
    use_case.execute.assert_called_once()
    compose.assert_called_once()
    factory.__exit__.assert_called_once()

    factory.__exit__.reset_mock()
    use_case.execute.reset_mock(side_effect=True)
    use_case.execute.return_value = ()
    with pytest.raises(KeyboardInterrupt):
        postgres_ci_entrypoint.run_postgres_ci_watch(
            config,
            app_config,
            checks=checks,
            sleep=Mock(side_effect=KeyboardInterrupt),
        )
    use_case.execute.assert_called_once()
    factory.__exit__.assert_called_once()


def test_composition_injects_once_and_presentation_maps_response(monkeypatch):
    """No runtime callback may reenter composition or leak a database adapter."""
    from agent_workflow.interfaces.cli.ci_controller import CiRunRequest, CiRunResponse

    factory, use_case, compose, config, app_config, checks = _watch_setup(monkeypatch)
    use_case.execute.side_effect = [
        (
            Namespace(verdict="SUCCESS"),
            Namespace(verdict="BLOCKED"),
            Namespace(verdict="FAILURE"),
        ),
        (),
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
        {"startup": True}, {"startup": False}
    ]
    factory.__exit__.assert_called_once()
