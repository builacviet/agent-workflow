"""Compatibility alias for the canonical PostgreSQL session factory."""

import sys
from typing import TYPE_CHECKING

from agent_workflow.infrastructure.database import session_factory

if TYPE_CHECKING:
    EXPECTED_REVISION = session_factory.EXPECTED_REVISION
    PostgresFactory = session_factory.PostgresFactory
    PostgresUnavailableError = session_factory.PostgresUnavailableError
else:
    # Legacy patches to create_engine or Session must reach the factory's globals.
    sys.modules[__name__] = session_factory
