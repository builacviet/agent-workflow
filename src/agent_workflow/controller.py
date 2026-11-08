"""Compatibility import for :mod:`agent_workflow.orchestration.controller`."""
import sys

if __name__ == "__main__":
    from agent_workflow.orchestration.controller import main

    main()
else:
    from agent_workflow.orchestration import controller as _implementation

    sys.modules[__name__] = _implementation
