"""Standalone Alembic migration runner; no Flask or runtime credentials."""
from __future__ import annotations

import os

from alembic import context
import sqlalchemy as sa
from sqlalchemy.pool import NullPool

config = context.config
target_metadata = None


def _dsn() -> str:
    value = os.environ.get("PG_MIGRATOR_DSN")
    if not value:
        raise RuntimeError("PG_MIGRATOR_DSN is required")
    try:
        url = sa.engine.make_url(value)
        if url.drivername != "postgresql+psycopg" or not url.username or not url.database:
            raise ValueError
    except (sa.exc.ArgumentError, ValueError):
        raise RuntimeError("PG_MIGRATOR_DSN must be an explicit PostgreSQL psycopg DSN") from None
    return value


def run_migrations_offline() -> None:
    raise RuntimeError("Offline migrations are disabled; use a reviewed live PostgreSQL database")


def run_migrations_online() -> None:
    engine = sa.create_engine(
        _dsn(), poolclass=NullPool, connect_args={"connect_timeout": 5}
    )
    try:
        with engine.begin() as connection:
            connection.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
            # Serializes concurrent schema migrators, NOT runtime Controllers.
            if not connection.execute(
                sa.text("SELECT pg_try_advisory_xact_lock(5727811, 1)")
            ).scalar_one():
                raise RuntimeError("another PostgreSQL schema migrator is active")
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                transaction_per_migration=False,
                transactional_ddl=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
