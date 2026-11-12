"""Validated application settings for composition boundaries.

Settings are read when from_env() is called, not at import time. No implicit
.env loading: deployment injects process environment; disposable local tests
may set their environment explicitly.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path, PureWindowsPath

from pydantic import BaseModel, ConfigDict, StrictBool


class AppConfig(BaseModel):
    """Typed, immutable settings object; extend here during Config Cleanup."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    ci_gate_required: StrictBool = True
    sandbox_state_dir: Path = Path.home() / ".agent-workflow" / "sandbox-fences"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "AppConfig":
        source = os.environ if environ is None else environ
        value = source.get("WFC_CI_GATE_REQUIRED", "1")
        if value not in {"0", "1"}:
            raise ValueError("WFC_CI_GATE_REQUIRED must be 0 or 1")
        state = source.get("WFC_SANDBOX_STATE_DIR")
        if state is None:
            return cls(ci_gate_required=value == "1")
        try:
            path = Path(state)
            windows = PureWindowsPath(state)
            if (
                not state or not path.is_absolute() or ".." in path.parts
                or ".." in windows.parts or "\x00" in state
                or "://" in state or (os.name != "nt" and windows.drive)
            ):
                raise ValueError
        except (ValueError, TypeError, OSError):
            raise ValueError("WFC_SANDBOX_STATE_DIR must be a safe absolute local path") from None
        return cls(ci_gate_required=value == "1", sandbox_state_dir=path)
