"""Layer: Composition
Responsibility: Resolve MCP invocation settings and assemble scoped dependencies.
Dependencies: AppConfig, Application ports and feature composition.
"""

from agent_workflow.app_config import AppConfig
from agent_workflow.application.techlead.ports import ITechLeadGitHub, ITechLeadNotion
from agent_workflow.composition.techlead_deps import (
    create_techlead_github,
    create_techlead_notion,
)

try:
    from agent_workflow.composition import postgres_mcp
except ModuleNotFoundError as exc:
    if exc.name != "sqlalchemy":
        raise
    # The MCP-only install still serves commands that do not need PostgreSQL.
    postgres_mcp = None


def _workflow_config() -> AppConfig:
    """Validate settings before resolving the optional PostgreSQL dependency."""
    config = AppConfig.from_env()
    if config.pg_runtime_dsn is None:
        raise ValueError("PG_RUNTIME_DSN is required")
    if postgres_mcp is None:
        raise ModuleNotFoundError("No module named 'sqlalchemy'", name="sqlalchemy")
    return config


def review_queue_provider():
    """Load settings for one authorized queue invocation."""
    config = _workflow_config()
    return postgres_mcp.postgres_mcp_review_queue(config)


def review_reads_provider():
    """Load settings for a single authorized review query."""
    config = _workflow_config()
    return postgres_mcp.postgres_mcp_review_reads(config)


def task_operations_provider():
    """Validate invocation settings before loading the PostgreSQL composition."""
    config = _workflow_config()
    return postgres_mcp.postgres_mcp_task_operations(config)


def review_writes_provider():
    """Load settings for one authorized review command."""
    config = _workflow_config()
    return postgres_mcp.postgres_mcp_review_writes(config)


def techlead_github_provider() -> ITechLeadGitHub:
    """Load settings and assemble a client only for an authorized invocation."""
    return create_techlead_github(AppConfig.techlead_github())


def techlead_notion_provider() -> ITechLeadNotion:
    """Load settings and assemble a client only for an authorized invocation."""
    return create_techlead_notion(AppConfig.techlead_notion())
