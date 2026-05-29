import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "jobs.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _safe_add_column(conn: sqlite3.Connection, table: str, column: str, col_def: str) -> None:
    """Add a column to a table if it does not already exist (safe ALTER TABLE)."""
    existing = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
        conn.commit()
        logger.info(f"Migration: added column {table}.{column}")


def init_db() -> None:
    """Idempotent schema setup. Safe to call on every startup."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(schema)
        conn.commit()
        # Safe migrations for databases created before these columns existed
        _safe_add_column(conn, "jobs", "deadline", "TEXT")
        logger.info(f"Database initialised at {DB_PATH}")
    except Exception:
        logger.exception("Database initialisation failed")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
