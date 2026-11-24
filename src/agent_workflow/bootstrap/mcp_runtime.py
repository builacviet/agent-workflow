"""Layer: Bootstrap
Responsibility: Load MCP invocation settings and expose scoped dependency providers.
Dependencies: AppConfig and PostgreSQL MCP composition.
"""

from agent_workflow.app_config import AppConfig

try:
    from agent_workflow.composition import postgres_mcp
except ModuleNotFoundError as exc:
    if exc.name != "sqlalchemy":
        raise
    # The MCP-only install still serves commands that do not need PostgreSQL.
    postgres_mcp = None


def postgres_review_reads_provider():
    """Load settings for a single authorized review query."""
    config = AppConfig.from_env()
    if config.pg_runtime_dsn is None:
        raise ValueError("PG_RUNTIME_DSN is required")
    if postgres_mcp is None:
        raise ModuleNotFoundError("No module named 'sqlalchemy'", name="sqlalchemy")
    return postgres_mcp.postgres_mcp_review_reads(config)


def postgres_workflow_provider():
    """Validate invocation settings before loading the PostgreSQL composition."""
    config = AppConfig.from_env()
    if config.pg_runtime_dsn is None:
        raise ValueError("PG_RUNTIME_DSN is required")

    from agent_workflow.composition.postgres_mcp import postgres_mcp_service

    return postgres_mcp_service(config)
