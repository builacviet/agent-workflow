"""Layer: Bootstrap
Responsibility: Expose the public Controller module command.
Dependencies: The PostgreSQL Controller bootstrap launcher.
"""

from agent_workflow.bootstrap.controller_entrypoint import main

if __name__ == "__main__":
    main()
