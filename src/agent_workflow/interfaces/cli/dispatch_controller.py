"""Presentation for a single PostgreSQL Dispatch pass."""

import argparse
import json
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from agent_workflow.app_config import AppConfig, validate_pg_runtime_dsn
from agent_workflow.application.dispatch.run_dispatch_pass import RunDispatchPassUseCase
from agent_workflow.application.exceptions import InvalidApplicationRequestError


@dataclass(frozen=True, slots=True)
class DispatchRunRequest:
    task_ids: frozenset[str]
    timeout_seconds: int


@dataclass(frozen=True, slots=True)
class DispatchRunResponse:
    worker_completed: bool


ComposeDispatch = Callable[
    [dict, AppConfig], AbstractContextManager[RunDispatchPassUseCase]
]


def add_dispatch_argument(parser: argparse.ArgumentParser) -> None:
    """Register the shared one-shot flag for PostgreSQL and legacy callers."""
    parser.add_argument("--once", action="store_true")


def read_controller_config(path: Path) -> object:
    return json.loads(path.resolve(strict=True).read_text(encoding="utf-8-sig"))


def run_dispatch_pass(
    config: object, app_config: AppConfig, *, compose: ComposeDispatch
) -> DispatchRunResponse:
    """Validate the request and invoke the Application pass in its resource scope."""
    validate_pg_runtime_dsn(app_config.pg_runtime_dsn)
    if not isinstance(config, dict):
        raise InvalidApplicationRequestError("dispatch configuration must be an object")
    tasks = config.get("tasks")
    if (
        not isinstance(tasks, dict)
        or not tasks
        or any(
            not isinstance(key, str) or not key.strip() or not isinstance(task, dict)
            for key, task in tasks.items()
        )
    ):
        raise InvalidApplicationRequestError("explicit task mapping required")
    timeout = config.get("timeout_seconds", 1800)
    if type(timeout) is not int or not 60 <= timeout <= 3600:
        raise InvalidApplicationRequestError("invalid dispatch timeout")
    request = DispatchRunRequest(frozenset(tasks), timeout)

    with compose(config, app_config) as use_case:
        completed = use_case.execute(set(request.task_ids), request.timeout_seconds)
    return DispatchRunResponse(worker_completed=completed)


def run_dispatch_command(
    *,
    once: bool,
    acknowledge: str | None,
    reconciled: bool,
    prepare: Callable[[], object],
    compose: ComposeDispatch,
) -> None:
    """Translate terminal failures and report only after the factory closes."""
    if not once or acknowledge is not None or reconciled:
        _finish_error(
            "PostgreSQL supports only explicit --once, --ci-once or --ci-watch"
        )

    try:
        try:
            app_config = AppConfig.from_env()
            if app_config.pg_runtime_dsn is None:
                raise ValueError("PG_RUNTIME_DSN is required")
            config = prepare()
        except (OSError, ValueError, TypeError):
            _finish_error(
                "PostgreSQL dispatch configuration is invalid or unavailable. "
                "Check PG_RUNTIME_DSN and the controller configuration."
            )
        result = run_dispatch_pass(config, app_config, compose=compose)
    except InvalidApplicationRequestError:
        _finish_error("Invalid PostgreSQL dispatch Worker configuration.")
    except KeyboardInterrupt:
        _finish_error("PostgreSQL dispatch interrupted.", exit_code=130)
    except Exception:
        _finish_error(
            "PostgreSQL dispatch failed. Inspect leadership and dispatch state "
            "before retrying."
        )

    print(
        "PostgreSQL dispatch: Worker completed."
        if result.worker_completed
        else "PostgreSQL dispatch: no work claimed.",
        flush=True,
    )


def _finish_error(message: str, *, exit_code: int = 1) -> NoReturn:
    print(message, file=sys.stderr, flush=True)
    raise SystemExit(exit_code)
