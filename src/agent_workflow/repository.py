"""Compatibility import for :mod:`agent_workflow.storage.repository`."""
import sys

from agent_workflow.storage import repository as _implementation

sys.modules[__name__] = _implementation
