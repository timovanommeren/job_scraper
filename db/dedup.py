import sqlite3
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)
DB_PATH = Path(__file__).parent / "jobs.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def fingerprint(url: str) -> str:
    """SHA-256 hash of canonical URL string."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def is_seen(url: str, conn: sqlite3.Connection) -> bool:
    """Returns True if this URL has already been processed."""
    h = fingerprint(url)
    row = conn.execute("SELECT 1 FROM jobs WHERE url_hash = ?", (h,)).fetchone()
    return row is not None


def update_last_seen(url: str, conn: sqlite3.Connection) -> None:
    """Update last_seen_at for a job that was scraped again but already known."""
    now = datetime.now(timezone.utc).isoformat()
    h = fingerprint(url)
    conn.execute(
        "UPDATE jobs SET last_seen_at = ?, is_active = 1 WHERE url_hash = ?",
        (now, h),
    )
    conn.commit()


def insert_job(raw, posting, conn: sqlite3.Connection) -> int:
    """Insert a newly processed job. Returns the new row id."""
    now = datetime.now(timezone.utc).isoformat()
    h = fingerprint(raw.url)
    cur = conn.execute(
        """INSERT INTO jobs
           (url, url_hash, source, title, organization, location, contract_type,
            deadline, description_snippet, tags, relevance_score, relevance_tier,
            relevance_reason, raw_text, first_seen_at, last_seen_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            raw.url, h, raw.source,
            posting.title, posting.organization, posting.location, posting.contract_type,
            posting.deadline, posting.description_snippet,
            json.dumps(posting.tags),
            posting.relevance_score, posting.relevance_tier, posting.relevance_reason,
            raw.raw_text[:8000],
            now, now,
        ),
    )
    conn.commit()
    return cur.lastrowid


def insert_failed(raw, error_msg: str, conn: sqlite3.Connection) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO failed_extractions (url, source, raw_text, error_msg, created_at) VALUES (?,?,?,?,?)",
        (raw.url, raw.source, raw.raw_text[:8000], error_msg, now),
    )
    conn.commit()


def mark_emailed(job_ids: list, conn: sqlite3.Connection) -> None:
    """Mark jobs as emailed after a successful send."""
    now = datetime.now(timezone.utc).isoformat()
    conn.executemany(
        "UPDATE jobs SET emailed_at = ? WHERE id = ?",
        [(now, jid) for jid in job_ids],
    )
    conn.commit()


def get_unemailed_jobs(conn: sqlite3.Connection) -> list:
    """Return all jobs not yet emailed, ordered by score descending."""
    return conn.execute(
        """SELECT * FROM jobs
           WHERE emailed_at IS NULL
             AND relevance_score IS NOT NULL
           ORDER BY relevance_score DESC, first_seen_at DESC"""
    ).fetchall()


def get_failed_extractions(limit: int, conn: sqlite3.Connection) -> list:
    """Return unretried failed extractions for reprocessing."""
    return conn.execute(
        "SELECT * FROM failed_extractions WHERE retried = 0 ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()


def mark_failed_retried(row_id: int, conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE failed_extractions SET retried = 1 WHERE id = ?", (row_id,))
    conn.commit()


def log_run_start(conn: sqlite3.Connection) -> int:
    """Insert a run_log row and return the new run id."""
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute("INSERT INTO run_log (started_at) VALUES (?)", (now,))
    conn.commit()
    return cur.lastrowid


def log_run_finish(
    run_id: int,
    sites_scraped: int,
    new_jobs_found: int,
    jobs_scored: int,
    jobs_emailed: int,
    api_errors: int,
    status: str,
    conn: sqlite3.Connection,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """UPDATE run_log SET
           finished_at=?, sites_scraped=?, new_jobs_found=?,
           jobs_scored=?, jobs_emailed=?, api_errors=?, status=?
           WHERE id=?""",
        (now, sites_scraped, new_jobs_found, jobs_scored, jobs_emailed, api_errors, status, run_id),
    )
    conn.commit()
