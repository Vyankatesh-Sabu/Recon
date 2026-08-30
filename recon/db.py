"""db.py — SQLite connection + idempotent migration runner.

Applies db/migrations/*.sql in filename order (CLAUDE.md rule 2, SPEC.md §4).
Idempotent: each applied filename is recorded in `schema_migrations`, so
re-running only applies files that haven't been seen yet.

Run directly to migrate the database in place: `python -m recon.db`.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path("data/recon.db")
MIGRATIONS_DIR = Path("db/migrations")

_SCHEMA_MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    TEXT PRIMARY KEY,
    applied_at  TEXT NOT NULL
)
"""


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    """Open a connection to the RECON-4 SQLite database, creating its parent dir if needed."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrate(
    db_path: Path | str = DB_PATH, migrations_dir: Path | str = MIGRATIONS_DIR
) -> list[str]:
    """Apply every not-yet-applied db/migrations/*.sql file, in filename order.

    Returns the list of migration filenames that were newly applied (empty if
    the schema was already up to date). Safe to call repeatedly.
    """
    migrations_dir = Path(migrations_dir)
    conn = connect(db_path)
    try:
        conn.execute(_SCHEMA_MIGRATIONS_TABLE)
        conn.commit()
        applied = {row[0] for row in conn.execute("SELECT filename FROM schema_migrations")}

        newly_applied: list[str] = []
        for path in sorted(migrations_dir.glob("*.sql")):
            if path.name in applied:
                continue
            conn.executescript(path.read_text())
            conn.execute(
                "INSERT INTO schema_migrations (filename, applied_at) VALUES (?, datetime('now'))",
                (path.name,),
            )
            conn.commit()
            newly_applied.append(path.name)
        return newly_applied
    finally:
        conn.close()


def latest_run_id(conn: sqlite3.Connection) -> str | None:
    """The most recently finished run's id, or None if no run has completed.

    Used by the Q&A tools (recon/llm/tools.py) so callers don't have to
    thread a run_id through every question by hand.
    """
    row = conn.execute(
        "SELECT run_id FROM runs WHERE finished_at IS NOT NULL ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


if __name__ == "__main__":
    _applied = migrate()
    if _applied:
        print(f"migrate: applied {', '.join(_applied)}")
    else:
        print("migrate: schema already up to date, nothing to apply")
