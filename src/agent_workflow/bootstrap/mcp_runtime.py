"""Provide a fresh PostgreSQL workflow scope for each authorized MCP call."""

import os

from agent_workflow.app_config import AppConfig


def postgres_workflow_provider():
    """Validate invocation settings before loading the PostgreSQL composition."""
    if os.environ.get("WFC_POC_NO_AUTH") == "1":
        raise PermissionError("PostgreSQL workflow tools require authenticated mode")

    config = AppConfig.from_env()
    if config.pg_runtime_dsn is None:
        raise ValueError("PG_RUNTIME_DSN is required")

    from agent_workflow.composition.postgres_mcp import postgres_mcp_service

    return postgres_mcp_service(config)
