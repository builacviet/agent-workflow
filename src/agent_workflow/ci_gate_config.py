"""Small CI Gate environment adapter; replace source during later Config Cleanup."""

from __future__ import annotations

import os


def load_ci_gate_required() -> bool:
    """Fail closed by default; reject anything other than literal 0 or 1."""
    value = os.environ.get("WFC_CI_GATE_REQUIRED", "1")
    if value not in {"0", "1"}:
        raise ValueError("WFC_CI_GATE_REQUIRED must be 0 or 1")
    return value == "1"
