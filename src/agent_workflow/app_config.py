"""Validated application settings for composition boundaries.

Settings are read when from_env() is called, not at import time. No implicit
.env loading: deployment injects process environment; disposable local tests
may set their environment explicitly.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from pathlib import Path, PureWindowsPath

from pydantic import BaseModel, ConfigDict, Field, StrictBool


def _windows_local_drive(anchor: str) -> bool:
    import ctypes

    # DRIVE_REMOVABLE and DRIVE_FIXED are local; reject remote and unknown drives.
    return ctypes.windll.kernel32.GetDriveTypeW(anchor) in (2, 3)


def _validate_state_dir(path: Path) -> Path:
    """Reject nonlocal paths and existing redirecting path components."""
    try:
        value = str(path)
        windows = PureWindowsPath(value)
        if (
            not value or not path.is_absolute() or ".." in path.parts
            or ".." in windows.parts or "\x00" in value or "://" in value
            or windows.drive.startswith(("\\", "/"))
            or (os.name != "nt" and windows.drive)
            or (os.name == "nt" and not _windows_local_drive(windows.anchor))
        ):
            raise ValueError
        for part in (path, *path.parents):
            try:
                info = part.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or (
                getattr(info, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            ):
                raise ValueError
    except (ValueError, TypeError, OSError, AttributeError):
        raise ValueError("WFC_SANDBOX_STATE_DIR must be a safe absolute local path") from None
    return path


class AppConfig(BaseModel):
    """Typed, immutable settings object; extend here during Config Cleanup."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    ci_gate_required: StrictBool = True
    sandbox_state_dir: Path = Field(
        default_factory=lambda: Path.home() / ".agent-workflow" / "sandbox-fences"
    )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "AppConfig":
        source = os.environ if environ is None else environ
        value = source.get("WFC_CI_GATE_REQUIRED", "1")
        if value not in {"0", "1"}:
            raise ValueError("WFC_CI_GATE_REQUIRED must be 0 or 1")
        state = source.get("WFC_SANDBOX_STATE_DIR")
        try:
            config = cls(ci_gate_required=value == "1") if state is None else cls(
                ci_gate_required=value == "1", sandbox_state_dir=Path(state)
            )
            _validate_state_dir(config.sandbox_state_dir)
        except (ValueError, TypeError, OSError):
            raise ValueError("WFC_SANDBOX_STATE_DIR must be a safe absolute local path") from None
        return config
