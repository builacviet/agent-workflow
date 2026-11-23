"""Layer: Interfaces/CLI
Responsibility: Validate Controller CLI input and route each supported mode.
Dependencies: Shared config reader, CI and Dispatch adapters, and injected providers.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from contextlib import AbstractContextManager
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

from agent_workflow.interfaces.cli import ci_controller, dispatch_controller
from agent_workflow.interfaces.cli.controller_config import read_controller_config

if TYPE_CHECKING:
    from agent_workflow.app_config import AppConfig


class _Mode(Enum):
    DISPATCH = auto()
    CI_ONCE = auto()
    CI_WATCH = auto()


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # argparse's default message can echo arbitrary argument values.
        super().error("Provide a config path and exactly one supported mode.")


def run_controller_cli(
    *,
    compose_ci: ci_controller.ComposeCi,
    compose_dispatch: dispatch_controller.ComposeDispatch,
    load_app_config: Callable[[], AppConfig],
    log_scope: Callable[[], AbstractContextManager[None]],
) -> None:
    parser = _ArgumentParser(
        description="Run PostgreSQL dispatch or reconcile CI.", allow_abbrev=False
    )
    parser.add_argument("config", type=Path)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument(
        "--once",
        dest="mode",
        action="store_const",
        const=_Mode.DISPATCH,
        help="run one dispatch pass",
    )
    modes.add_argument(
        "--ci-once",
        dest="mode",
        action="store_const",
        const=_Mode.CI_ONCE,
        help="reconcile CI once without dispatching agents",
    )
    modes.add_argument(
        "--ci-watch",
        dest="mode",
        action="store_const",
        const=_Mode.CI_WATCH,
        help="consume the CI inbox without dispatching agents",
    )
    args = parser.parse_args()
    mode: _Mode = args.mode
    config_path: Path = args.config

    def prepare() -> object:
        config = read_controller_config(config_path)
        if isinstance(config, dict) and "database" in config:
            raise ValueError("SQLite database settings are unsupported")
        return config

    if mode is _Mode.DISPATCH:
        dispatch_controller.run_dispatch_command(
            once=True,
            acknowledge=None,
            reconciled=False,
            prepare=prepare,
            compose=compose_dispatch,
        )
        return

    ci_controller.run_ci_command(
        ci_once=mode is _Mode.CI_ONCE,
        ci_watch=mode is _Mode.CI_WATCH,
        dispatch_once=False,
        acknowledge=None,
        reconciled=False,
        prepare=lambda: (prepare(), load_app_config()),
        compose=compose_ci,
        log_scope=log_scope,
    )
