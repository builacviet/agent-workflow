"""Compatibility import for :mod:`agent_workflow.mcp.security`."""
import sys

from agent_workflow.mcp import security as _implementation

sys.modules[__name__] = _implementation
