"""Compatibility import for :mod:`agent_workflow.mcp.auth0_oauth`."""
import sys

from agent_workflow.mcp import auth0_oauth as _implementation

sys.modules[__name__] = _implementation
