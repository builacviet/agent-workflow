"""Compatibility import for :mod:`agent_workflow.mcp.tools`."""
import sys

from agent_workflow.mcp import tools as _implementation

sys.modules[__name__] = _implementation
