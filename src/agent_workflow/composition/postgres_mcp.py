"""Inert, scoped PostgreSQL workflow service for future MCP composition."""

from collections.abc import Iterator
from contextlib import contextmanager

from agent_workflow.app_config import AppConfig
from agent_workflow.domain.service import WorkflowService
from agent_workflow.storage.postgres_connection import PostgresFactory
from agent_workflow.storage.postgres_workflow import PostgresWorkflowRepository


@contextmanager
def postgres_mcp_service(app_config: AppConfig) -> Iterator[WorkflowService]:
    """Own the factory for the lifetime of the yielded service."""
    factory = PostgresFactory.open(app_config)
    try:
        yield WorkflowService(
            repository=PostgresWorkflowRepository(factory),
            require_ci=app_config.ci_gate_required,
        )
    finally:
        factory.close()
