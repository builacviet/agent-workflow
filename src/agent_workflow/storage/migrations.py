"""One atomic, versioned SQLite migration mechanism shared by storage adapters."""
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import sqlite3


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    upgrade: Callable[[sqlite3.Connection], None]


def table_columns(db: sqlite3.Connection, table: str) -> tuple:
    """Table names are internal constants; never supply user input."""
    return tuple(
        (row[1], row[2].upper(), row[3], row[5])
        for row in db.execute(f"PRAGMA table_info({table})")
    )


_MIGRATIONS_COLUMNS = (
    ("component", "TEXT", 1, 1),
    ("version", "INTEGER", 1, 2),
    ("name", "TEXT", 1, 0),
    ("applied_at", "TEXT", 1, 0),
)


def table_names(db: sqlite3.Connection) -> set[str]:
    return {
        row[0] for row in db.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
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
    validate: Callable[[sqlite3.Connection], None],
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
            db.execute("""
                CREATE TABLE schema_migrations (
                    component TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(component, version)
                )
            """)
        if table_columns(db, "schema_migrations") != _MIGRATIONS_COLUMNS:
            raise sqlite3.DatabaseError("incompatible schema_migrations table")
        recorded = tuple(db.execute(
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

        for step in migrations[adopted:]:
            step.upgrade(db)
            db.execute(
                "INSERT INTO schema_migrations(component, version, name) "
                "VALUES (?, ?, ?)", (component, step.version, step.name)
            )
        validate(db)
        db.commit()
    except Exception:
        db.rollback()
        raise
