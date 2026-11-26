"""Typed Presentation boundary for Controller CI one-shot and watch commands.

The CLI owns argument interpretation, result display and retry scheduling.
The injected Application use case performs the guarded reconciliation.
"""

import argparse
import logging
import os
import sys
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, ExitStack
from dataclasses import dataclass

from agent_workflow.app_config import AppConfig
from agent_workflow.application.ci.use_cases.reconcile_ci import ReconcileCiUseCase
from agent_workflow.application.exceptions import (
    ApplicationError,
    CorruptedPersistenceDataError,
    InvalidApplicationRequestError,
    PersistenceUnavailableError,
)
from agent_workflow.domain.exceptions import DomainError
from agent_workflow.observability.correlation import correlation_scope


@dataclass(frozen=True, slots=True)
class CiRunRequest:
    """One Controller CI pass; startup may recheck durable tracked work."""

    startup: bool


@dataclass(frozen=True, slots=True)
class CiRunResponse:
    """Presentation projection of the durable, completed CI pass."""

    observed_count: int
    blocked_count: int


ComposeCi = Callable[[dict, AppConfig], AbstractContextManager[ReconcileCiUseCase]]
sleep = time.sleep
_LOGGER = logging.getLogger("agent_workflow.interfaces.cli.ci_controller")


def run_ci_pass(use_case: ReconcileCiUseCase, request: CiRunRequest) -> CiRunResponse:
    """Invoke the injected use case and map its evidence to the CLI response."""
    evidence = use_case.execute(startup=request.startup)
    return CiRunResponse(
        observed_count=len(evidence),
        blocked_count=sum(item.verdict == "BLOCKED" for item in evidence),
    )


def add_ci_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the two explicit CI-only operator modes on the shared parser."""
    parser.add_argument(
        "--ci-once",
        action="store_true",
        help="reconcile CI only, never dispatch agents",
    )
    parser.add_argument(
        "--ci-watch",
        action="store_true",
        help="consume local inbox, never dispatch agents",
    )


def run_ci_once(use_case: ReconcileCiUseCase) -> None:
    """Print only after a successful committed pass."""
    _print_ci_once(run_ci_pass(use_case, CiRunRequest(startup=True)))


def _print_ci_once(result: CiRunResponse) -> None:
    print(
        f"CI reconciliation: {result.observed_count} tracked observations",
        flush=True,
    )


def run_ci_watch(
    use_case: ReconcileCiUseCase,
    *,
    sleep: Callable[[float], None] = sleep,
    stop: Callable[[], bool] | None = None,
) -> None:
    """Bounded backoff on BLOCKED; never swallow a lost leadership exception."""
    delay = 2
    startup = True
    while stop is None or not stop():
        result = run_ci_pass(use_case, CiRunRequest(startup=startup))
        startup = False
        delay = min(60, delay * 2) if result.blocked_count else 2
        if stop is not None and stop():
            break
        sleep(delay)


def run_ci_command(
    *,
    ci_once: bool,
    ci_watch: bool,
    dispatch_once: bool,
    acknowledge: str | None,
    reconciled: bool,
    prepare: Callable[[], tuple[object, AppConfig]],
    compose: ComposeCi,
    log_scope: Callable[[], AbstractContextManager[None]],
) -> None:
    """Run a terminal CI command and translate failures at the CLI boundary.

    The lower-level run_ci_once/run_ci_watch APIs deliberately keep propagating
    exceptions for callers that compose them directly.
    """
    mode = "watch" if ci_watch else "once"
    with correlation_scope(correlation_id=os.environ.get("CORRELATION_ID")):
        with log_scope():
            if (
                ci_once == ci_watch
                or dispatch_once
                or acknowledge is not None
                or reconciled
            ):
                _finish_error(
                    mode,
                    "invalid_request",
                    "Use exactly one CI mode without dispatch acknowledgement options.",
                    2,
                )
            if "PG_RUNTIME_DSN" not in os.environ:
                _finish_error(
                    mode,
                    "invalid_configuration",
                    "PG_RUNTIME_DSN is required for CI commands.",
                    2,
                )

            try:
                config, app_config = prepare()
            except (OSError, UnicodeError, ValueError, TypeError):
                _finish_error(
                    mode,
                    "invalid_configuration",
                    "CI configuration is invalid or unavailable.",
                    2,
                )
            if not isinstance(config, dict):
                _finish_error(
                    mode,
                    "invalid_configuration",
                    "CI configuration is invalid or unavailable.",
                    2,
                )

            try:
                with ExitStack() as resources:
                    try:
                        use_case = resources.enter_context(compose(config, app_config))
                    except ValueError:
                        _finish_error(
                            mode,
                            "invalid_configuration",
                            "Controller CI configuration is invalid.",
                            2,
                        )
                    if ci_once:
                        result = run_ci_pass(use_case, CiRunRequest(startup=True))
                    else:
                        run_ci_watch(use_case)
                if ci_once:
                    _print_ci_once(result)
            except KeyboardInterrupt:
                _finish_error(
                    mode,
                    "interrupted",
                    "CI watch interrupted; stopping."
                    if ci_watch
                    else "CI command interrupted; stopping.",
                    130,
                    level=logging.INFO,
                )
            except PersistenceUnavailableError:
                _finish_error(
                    mode,
                    "persistence_unavailable",
                    "CI persistence is temporarily unavailable.",
                    75,
                )
            except TimeoutError:
                _finish_error(
                    mode,
                    "timeout",
                    "The CI operation timed out.",
                    75,
                )
            except (InvalidApplicationRequestError, DomainError):
                _finish_error(
                    mode,
                    "request_rejected",
                    "The CI request was rejected.",
                    2,
                )
            except CorruptedPersistenceDataError:
                _finish_error(
                    mode,
                    "persistence_data_invalid",
                    "Stored CI data failed validation.",
                    1,
                )
            except PermissionError:
                _finish_error(
                    mode,
                    "permission_failure",
                    "CI operation encountered a permission or ownership failure; "
                    "stop and reconcile in-flight work.",
                    1,
                )
            except ApplicationError:
                _finish_error(
                    mode,
                    "application_failure",
                    "CI operation failed at an application boundary; inspect "
                    "service and leadership state.",
                    1,
                )
            except OSError:
                _finish_error(
                    mode,
                    "integration_failure",
                    "A required CI service is unavailable.",
                    1,
                )
            except Exception:
                _finish_error(
                    mode,
                    "unexpected_failure",
                    "CI operation failed; inspect the sanitized application logs.",
                    1,
                )


def _finish_error(
    mode: str,
    reason: str,
    message: str,
    exit_code: int,
    *,
    level: int = logging.ERROR,
) -> None:
    _LOGGER.log(
        level,
        "ci_cli_failure",
        extra={"mode": mode, "reason": reason},
    )
    print(message, file=sys.stderr, flush=True)
    raise SystemExit(exit_code)
