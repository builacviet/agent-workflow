"""Shared PostgreSQL transaction settings; no transaction ownership."""

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from agent_workflow.budget import operation_timeout

WORKFLOW_QUERY_TIMEOUT_SECONDS = 30
REPOSITORY_LOCK_TIMEOUT_SECONDS = 5
REPOSITORY_STATEMENT_TIMEOUT_SECONDS = 5
MIN_QUERY_TIMEOUT_MS = 1


def set_workflow_query_timeout(session: Session) -> None:
    # PostgreSQL interprets zero as unlimited; never round a budget down to zero.
    milliseconds = max(
        MIN_QUERY_TIMEOUT_MS,
        int(operation_timeout(WORKFLOW_QUERY_TIMEOUT_SECONDS) * 1000),
    )
    session.connection().execute(
        text(
            "SELECT set_config('lock_timeout', :timeout, true), "
            "set_config('statement_timeout', :timeout, true)"
        ),
        {"timeout": f"{milliseconds}ms"},
    )


def set_repository_transaction_options(
    connection: Connection, *, read_only: bool
) -> None:
    # Webhook collision lookup must see the winner committed by a concurrent insert.
    mode = ", READ ONLY" if read_only else ""
    connection.execute(text(f"SET TRANSACTION ISOLATION LEVEL READ COMMITTED{mode}"))
    connection.execute(
        text(
            "SELECT set_config('lock_timeout', :lock_timeout, true), "
            "set_config('statement_timeout', :statement_timeout, true), "
            "set_config('synchronous_commit', :synchronous_commit, true)"
        ),
        {
            "lock_timeout": f"{REPOSITORY_LOCK_TIMEOUT_SECONDS}s",
            "statement_timeout": f"{REPOSITORY_STATEMENT_TIMEOUT_SECONDS}s",
            "synchronous_commit": "on",
        },
    )
