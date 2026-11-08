"""Compatibility import for :mod:`agent_workflow.domain.service`."""
import sys

from agent_workflow.domain import service as _implementation

sys.modules[__name__] = _implementation
