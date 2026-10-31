"""Apply the v1 baseline and versioned migrations.

The v1 baseline is ``app/db/schema.sql`` (jobs + artifacts), recorded as
``schema_migrations(1, 'base')``. ``migrations/002_ai_tables.sql`` is v2.
The old member-5 001 schema is retired: a database that shows it is rejected
before any DDL, not silently migrated.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Iterable

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
MIGRATIONS_DIR = Path(__file__).with_name("migrations")
BASELINE_VERSION = 1
BASELINE_NAME = "base"


class LegacySchemaError(RuntimeError):
    """A pre-baseline (001) or otherwise incompatible database."""


class DuplicateArtifactsError(RuntimeError):
    """``(job_id, relative_path)`` is not unique; migration refuses to delete."""

    def __init__(self, rows: Iterable[tuple[str, str, int]]) -> None:
        self.rows = list(rows)
        detail = ", ".join(f"{job}/{path} x{n}" for job, path, n in self.rows)
        super().__init__(
            "duplicate artifacts block the unique index; resolve manually: " + detail
        )


def _open(data_dir: str | Path) -> sqlite3.Connection:
    directory = Path(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(directory / "app.db"))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _ensure_registry(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    connection.commit()


def _reject_legacy(connection: sqlite3.Connection) -> None:
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    if "jobs" in tables:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
        if "fixture_id" in columns:
            raise LegacySchemaError(
                "legacy 001 schema detected (jobs.fixture_id); "
                "delete var/app.db and recreate; no automatic migration"
            )
    row = connection.execute(
        "SELECT name FROM schema_migrations WHERE version = ?", (BASELINE_VERSION,)
    ).fetchone()
    if row is not None and row[0] != BASELINE_NAME:
        raise LegacySchemaError(
            "legacy 001 schema detected (schema_migrations v1 name); "
            "delete var/app.db and recreate; no automatic migration"
        )
    if row is None:
        later = connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version > ?", (BASELINE_VERSION,)
        ).fetchone()
        if later is not None:
            raise LegacySchemaError(
                "schema_migrations has a later version but no v1 baseline; "
                "delete var/app.db and recreate"
            )


def _record_baseline(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, name, applied_at) "
        "VALUES (?, ?, datetime('now'))",
        (BASELINE_VERSION, BASELINE_NAME),
    )
    connection.commit()


def _preflight_artifacts(connection: sqlite3.Connection) -> None:
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    if "artifacts" not in tables:
        return
    rows = connection.execute(
        """
        SELECT job_id, relative_path, COUNT(*) AS n
        FROM artifacts
        GROUP BY job_id, relative_path
        HAVING n > 1
        ORDER BY job_id, relative_path
        """
    ).fetchall()
    if rows:
        raise DuplicateArtifactsError(
            (row["job_id"], row["relative_path"], row["n"]) for row in rows
        )


def _migration_files() -> list[tuple[int, Path]]:
    out = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        prefix = path.name.split("_", 1)[0]
        if not prefix.isdigit():
            raise RuntimeError(f"migration name must start with a version: {path.name}")
        out.append((int(prefix), path))
    return out


def apply_migrations(connection: sqlite3.Connection) -> int:
    current = connection.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
    ).fetchone()[0]
    applied = 0
    for version, path in _migration_files():
        if version <= current:
            continue
        connection.executescript(path.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO schema_migrations (version, name, applied_at) "
            "VALUES (?, ?, datetime('now'))",
            (version, path.stem),
        )
        connection.commit()
        applied += 1
        current = version
    return applied


def init_db(data_dir: str | Path) -> sqlite3.Connection:
    """Open ``<data_dir>/app.db`` and bring it to the latest schema."""
    connection = _open(data_dir)
    _ensure_registry(connection)
    _reject_legacy(connection)
    connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _record_baseline(connection)
    _preflight_artifacts(connection)
    apply_migrations(connection)
    return connection


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply BinaryRepair DB migrations")
    parser.add_argument("--db", default="var", help="data directory (contains app.db)")
    args = parser.parse_args()
    connection = init_db(args.db)
    versions = [row[0] for row in connection.execute(
        "SELECT version FROM schema_migrations ORDER BY version"
    )]
    print(f"applied; schema versions: {versions}")
    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
