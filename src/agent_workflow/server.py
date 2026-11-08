"""Compatibility import for :mod:`agent_workflow.mcp.server`."""
import sys

if __name__ == "__main__":
    from agent_workflow.mcp.server import main

    main()
else:
    from agent_workflow.mcp import server as _implementation

    sys.modules[__name__] = _implementation
