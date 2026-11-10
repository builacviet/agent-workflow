"""One atomic, versioned SQLite migration mechanism shared by storage adapters."""
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass
import re
import sqlite3


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    upgrade: Callable[[sqlite3.Connection], None]


_REGISTRY_SQL = """
    CREATE TABLE schema_migrations (
        component TEXT NOT NULL,
        version INTEGER NOT NULL,
        name TEXT NOT NULL,
        applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(component, version)
    )
"""
_SQL_TOKEN = re.compile(
    r"""--[^\n]*|/\*[\s\S]*?\*/|'(?:''|[^'])*'|"(?:""|[^"])*"|"""
    r"""(?:\x60(?:\x60\x60|[^\x60])*\x60)|\[[^\]]*\]|[ \t\n\f\r]+|[a-zA-Z0-9_$\x80-\U0010FFFF]+|."""
)


def _sql_tokens(sql: str | None) -> tuple[str, ...] | None:
    if sql is None:
        return None
    return tuple(
        token if not token.isascii() or token[0] in "'\"[`" else token.lower()
        for token in _SQL_TOKEN.findall(sql)
        if token[0] not in " \t\n\f\r" and not token.startswith(("--", "/*"))
    )


def validate_schema(
    db: sqlite3.Connection,
    expected_db: sqlite3.Connection,
    component: str,
    *,
    retired_tables: tuple[str, ...] = (),
) -> None:
    """Compare owned tables and their attached indexes and triggers."""
    query = "SELECT type, name, tbl_name, sql FROM main.sqlite_schema"
    expected = {
        (kind, name.lower(), table.lower()): _sql_tokens(sql)
        for kind, name, table, sql in expected_db.execute(query)
    }
    owned_tables = {table for _, _, table in expected} | set(retired_tables)
    owned_names = {name for _, name, _ in expected} | owned_tables
    actual = {
        (kind, name.lower(), table.lower()): _sql_tokens(sql)
        for kind, name, table, sql in db.execute(query)
        if table.lower() in owned_tables or name.lower() in owned_names
    }
    if actual != expected:
        raise sqlite3.DatabaseError(f"incompatible {component} schema")


def table_names(db: sqlite3.Connection) -> set[str]:
    return {
        row[0].lower() for row in db.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT GLOB 'sqlite_*'"
        )
    }


def execute_sql_script(db: sqlite3.Connection, source: str) -> None:
    """Execute a bundled DDL script without executescript's implicit COMMIT."""
    buffer = ""
    for line in source.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                db.execute(statement)
            buffer = ""
    if buffer.strip():
        raise sqlite3.DatabaseError("incomplete SQLite migration SQL")


def migrate(
    db: sqlite3.Connection,
    component: str,
    migrations: Sequence[Migration],
    *,
    adopt_legacy: Callable[[sqlite3.Connection], int],
    validate: Callable[[sqlite3.Connection, int], None],
) -> None:
    """Upgrade a component in one transaction, preserving other components."""
    if not component or not migrations:
        raise ValueError("component and migrations are required")
    if any(
        migration.version != index or not migration.name
        for index, migration in enumerate(migrations, start=1)
    ):
        raise ValueError("migration versions must be a contiguous sequence")

    db.execute("BEGIN IMMEDIATE")
    try:
        if "schema_migrations" not in table_names(db):
            db.execute(_REGISTRY_SQL)
        with closing(sqlite3.connect(":memory:")) as expected:
            expected.execute(_REGISTRY_SQL)
            validate_schema(db, expected, "schema_migrations")
        # sqlite3.Row (used by the workflow adapter) does not compare
        # equal to tuples. Normalize recorded evidence for all adapters.
        recorded = tuple(tuple(row) for row in db.execute(
            "SELECT version, name FROM schema_migrations "
            "WHERE component = ? ORDER BY version", (component,)
        ))
        expected_prefix = tuple(
            (step.version, step.name) for step in migrations[:len(recorded)]
        )
        if recorded != expected_prefix:
            raise sqlite3.DatabaseError("unknown, missing or altered migrations")

        if not recorded:
            adopted = adopt_legacy(db)
            if type(adopted) is not int or not 0 <= adopted <= len(migrations):
                raise sqlite3.DatabaseError("invalid legacy migration state")
            for step in migrations[:adopted]:
                db.execute(
                    "INSERT INTO schema_migrations(component, version, name) "
                    "VALUES (?, ?, ?)", (component, step.version, step.name)
                )
        else:
            adopted = len(recorded)
            if adopted < len(migrations):
                validate(db, adopted)

        for step in migrations[adopted:]:
            step.upgrade(db)
            db.execute(
                "INSERT INTO schema_migrations(component, version, name) "
                "VALUES (?, ?, ?)", (component, step.version, step.name)
            )
        validate(db, len(migrations))
        db.commit()
    except Exception:
        db.rollback()
        raise
