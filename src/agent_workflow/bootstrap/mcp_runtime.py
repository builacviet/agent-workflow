"""Provide a fresh PostgreSQL workflow scope for each authorized MCP call."""

from agent_workflow.app_config import AppConfig


def postgres_workflow_provider():
    """Validate invocation settings before loading the PostgreSQL composition."""
    config = AppConfig.from_env()
    if config.pg_runtime_dsn is None:
        raise ValueError("PG_RUNTIME_DSN is required")

    from agent_workflow.composition.postgres_mcp import postgres_mcp_service

    return postgres_mcp_service(config)
