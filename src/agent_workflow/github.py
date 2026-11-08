"""Compatibility import for :mod:`agent_workflow.integrations.github`."""
import sys

from agent_workflow.integrations import github as _implementation

sys.modules[__name__] = _implementation
