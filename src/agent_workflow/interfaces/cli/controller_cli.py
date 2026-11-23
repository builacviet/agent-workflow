"""Route explicit PostgreSQL Controller commands to their inbound adapters."""

import argparse
from collections.abc import Callable
from contextlib import AbstractContextManager
from enum import Enum, auto
from pathlib import Path
from typing import NoReturn

from agent_workflow.app_config import AppConfig
from agent_workflow.interfaces.cli import ci_controller, dispatch_controller


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
    log_scope: Callable[[], AbstractContextManager[None]],
) -> None:
    parser = _ArgumentParser(description=__doc__, allow_abbrev=False)
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
        config = dispatch_controller.read_controller_config(config_path)
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
        prepare=lambda: (prepare(), AppConfig.from_env()),
        compose=compose_ci,
        log_scope=log_scope,
    )
