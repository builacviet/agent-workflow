"""Compatibility import for :mod:`agent_workflow.storage.dispatch_repository`."""
import sys

from agent_workflow.storage import dispatch_repository as _implementation

sys.modules[__name__] = _implementation
