"""Migration runner shared by all modules.

Migrations live as `.sql` files in a per-module package, named `NNN_<name>.sql`.
The runner tracks `schema_version` per (db, module) pair.
"""
from __future__ import annotations

import sqlite3
from importlib import resources
from importlib.resources.abc import Traversable

_STORAGE_MODULE_KEY = "storage"


def apply_migrations(
    conn: sqlite3.Connection,
    *,
    module: str,
    migrations: list[tuple[int, str]],
) -> None:
    """Apply pending migrations idempotently.

    `migrations` is a list of `(version, sql_text)` pairs. Versions already
    recorded in `schema_version` for `module` are skipped.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            module  TEXT PRIMARY KEY,
            version INTEGER NOT NULL
        )
        """
    )

    row = conn.execute(
        "SELECT version FROM schema_version WHERE module = ?",
        (module,),
    ).fetchone()
    current = row[0] if row else 0

    for version, sql in sorted(migrations):
        if version <= current:
            continue
        statements = _split_sql(sql)
        conn.execute("BEGIN IMMEDIATE")
        try:
            for stmt in statements:
                conn.execute(stmt)
            conn.execute(
                "INSERT INTO schema_version (module, version) VALUES (?, ?) "
                "ON CONFLICT(module) DO UPDATE SET version = excluded.version",
                (module, version),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def load_migrations_from_package(package: str) -> list[tuple[int, str]]:
    """Read all bundled ``NNN_*.sql`` files from a Python package."""
    out: list[tuple[int, str]] = []
    for entry in resources.files(package).iterdir():
        if _is_sql_file(entry):
            version = int(entry.name.split("_", 1)[0])
            out.append((version, entry.read_text(encoding="utf-8")))
    return sorted(out)


def load_storage_migrations(scope: str) -> list[tuple[int, str]]:
    """Read Storage's bundled `.sql` files for the given scope (`board` | `company`)."""
    if scope not in ("board", "company"):
        raise ValueError(f"unknown scope: {scope!r}")
    return load_migrations_from_package(f"modules.storage.migrations.{scope}")


def run_storage_migrations(conn: sqlite3.Connection, scope: str) -> None:
    """Convenience wrapper used by `CompanyDB.migrate` / `BoardDB.migrate`."""
    apply_migrations(
        conn,
        module=_STORAGE_MODULE_KEY,
        migrations=load_storage_migrations(scope),
    )


def _is_sql_file(entry: Traversable) -> bool:
    return entry.is_file() and entry.name.endswith(".sql")


def _split_sql(sql: str) -> list[str]:
    """Strip ``-- line`` comments and split into individual statements.

    Adequate for the simple DDL migrations bundled with this project; do not
    use on SQL containing ``--`` inside string literals.
    """
    cleaned_lines: list[str] = []
    for line in sql.splitlines():
        idx = line.find("--")
        if idx >= 0:
            line = line[:idx]
        cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)
    return [stmt.strip() for stmt in text.split(";") if stmt.strip()]
