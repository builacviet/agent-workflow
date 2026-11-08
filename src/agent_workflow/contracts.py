"""Compatibility import for :mod:`agent_workflow.domain.contracts`."""
import sys

from agent_workflow.domain import contracts as _implementation

sys.modules[__name__] = _implementation
