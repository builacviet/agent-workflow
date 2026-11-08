"""Compatibility import for :mod:`agent_workflow.runners.coder_runtime`."""
import sys

from agent_workflow.runners import coder_runtime as _implementation

sys.modules[__name__] = _implementation
