"""Shared PostgreSQL transaction settings; no transaction ownership."""

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from agent_workflow.budget import operation_timeout


def set_workflow_query_timeout(session: Session) -> None:
    # PostgreSQL interprets zero as unlimited; never round a budget down to zero.
    milliseconds = max(1, int(operation_timeout(30) * 1000))
    session.connection().execute(
        text(
            "SELECT set_config('lock_timeout', :timeout, true), "
            "set_config('statement_timeout', :timeout, true)"
        ),
        {"timeout": f"{milliseconds}ms"},
    )


def set_repository_transaction_options(connection: Connection, *, read_only: bool) -> None:
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
            "lock_timeout": "5s",
            "statement_timeout": "5s",
            "synchronous_commit": "on",
        },
    )
