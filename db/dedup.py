import re
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


def content_fingerprint(title: str, org: str) -> str:
    """SHA-256 of normalized title+org for cross-source dedup (L2).

    Normalization: lowercase → replace punctuation with space → collapse whitespace.
    "PhD Candidate - Methodology" and "PhD Candidate: Methodology" hash identically.
    """
    def _norm(s: str) -> str:
        s = re.sub(r"[^a-z0-9]", " ", (s or "").lower())
        return re.sub(r"\s+", " ", s).strip()
    key = _norm(title) + "|" + _norm(org)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def is_seen(url: str, conn: sqlite3.Connection,
            title: str = "", org: str = "") -> str:
    """
    Returns 'scored' if URL is in jobs, 'filtered' if in non-expired filtered_jobs,
    or 'new' if unseen.

    L1 — url_hash: exact URL match (existing behaviour, O(1)).
    L2 — content_hash: cross-source dedup on normalize(title+org). Only checked
         when title is provided; skips without raising if content_hash column is
         absent (migration not yet run).

    WARNING: all three values are truthy strings. Call sites must use explicit
    string comparison — never `if is_seen():`.
    """
    h = fingerprint(url)
    if conn.execute("SELECT 1 FROM jobs WHERE url_hash = ?", (h,)).fetchone():
        return "scored"
    # L2: content_hash cross-source dedup
    if title:
        try:
            ch = content_fingerprint(title, org)
            if conn.execute("SELECT 1 FROM jobs WHERE content_hash = ?", (ch,)).fetchone():
                return "scored"
        except Exception:
            pass  # column absent before migration — safe to skip
    row = conn.execute(
        "SELECT 1 FROM filtered_jobs WHERE url_hash = ? AND expires_at > strftime('%Y-%m-%d %H:%M:%S', 'now')",
        (h,),
    ).fetchone()
    if row:
        return "filtered"
    return "new"


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
    ch = content_fingerprint(posting.title or "", posting.organization or "")
    cur = conn.execute(
        """INSERT INTO jobs
           (url, url_hash, content_hash, source, title, organization, location,
            contract_type, deadline, description_snippet, tags, relevance_score,
            relevance_tier, relevance_reason, raw_text, first_seen_at, last_seen_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            raw.url, h, ch, raw.source,
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


def insert_filtered(
    raw,
    filter_stage: str,
    filter_reason: str,
    conn: sqlite3.Connection,
    similarity: float | None = None,
) -> None:
    """
    Record a job rejected before LLM scoring.
    Uses INSERT OR IGNORE to handle cross-listed URLs from multiple scrapers.
    expires_at stored in SQLite's native space-separated format (see A3 note in schema.sql).
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    expires = (datetime.now(timezone.utc) + __import__("datetime").timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    h = fingerprint(raw.url)
    max_chars = int(__import__("os").environ.get("RAW_TEXT_MAX_CHARS", 4000))
    conn.execute(
        """INSERT OR IGNORE INTO filtered_jobs
           (url, url_hash, source, title, organization, raw_text,
            filter_stage, filter_reason, similarity, filtered_at, expires_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            raw.url, h, raw.source,
            getattr(raw, "title", None),
            getattr(raw, "organization", None),
            (raw.raw_text or "")[:max_chars],
            filter_stage, filter_reason, similarity, now, expires,
        ),
    )
    conn.commit()


def log_view(job_id: int, conn: sqlite3.Connection) -> None:
    """Record a Flask job-detail page view (implicit positive signal for ranker)."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO job_views (job_id, viewed_at) VALUES (?, ?)",
        (job_id, now),
    )
    conn.commit()


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
    jobs_filtered: int,
    jobs_emailed: int,
    api_errors: int,
    pre_screen_errors: int,
    status: str,
    conn: sqlite3.Connection,
    source_yields: dict | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    est_cost_eur: float = 0.0,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """UPDATE run_log SET
           finished_at=?, sites_scraped=?, new_jobs_found=?,
           jobs_scored=?, jobs_filtered=?, jobs_emailed=?,
           api_errors=?, pre_screen_errors=?, status=?, source_yields=?,
           input_tokens=?, output_tokens=?, cache_read_tokens=?,
           cache_creation_tokens=?, est_cost_eur=?
           WHERE id=?""",
        (now, sites_scraped, new_jobs_found, jobs_scored, jobs_filtered,
         jobs_emailed, api_errors, pre_screen_errors, status,
         json.dumps(source_yields or {}),
         input_tokens, output_tokens, cache_read_tokens,
         cache_creation_tokens, est_cost_eur, run_id),
    )
    conn.commit()


def get_run_stats(n_days: int, conn: sqlite3.Connection) -> dict:
    """
    Return aggregate stats for the last n_days for the weekly health report.
    Queries run_log and feedback tables.
    """
    cutoff = (datetime.now(timezone.utc) - __import__("datetime").timedelta(days=n_days)).isoformat()
    rows = conn.execute(
        """SELECT jobs_scored, jobs_filtered, api_errors, pre_screen_errors, status
           FROM run_log
           WHERE started_at >= ? AND finished_at IS NOT NULL""",
        (cutoff,),
    ).fetchall()

    total_scored = sum(r["jobs_scored"] or 0 for r in rows)
    total_filtered = sum(r["jobs_filtered"] or 0 for r in rows)
    total_api_errors = sum(r["api_errors"] or 0 for r in rows)
    total_pre_screen_errors = sum(r["pre_screen_errors"] or 0 for r in rows)
    run_statuses = [r["status"] for r in rows]
    failed_runs = sum(1 for s in run_statuses if s in ("failed", "partial"))

    labeled = conn.execute(
        "SELECT COUNT(*) FROM feedback WHERE timestamp >= ?", (cutoff,)
    ).fetchone()[0]

    cost_usd = total_filtered * 0.0001 + total_scored * 0.001

    return {
        "n_runs": len(rows),
        "failed_runs": failed_runs,
        "total_scored": total_scored,
        "total_filtered": total_filtered,
        "total_api_errors": total_api_errors,
        "total_pre_screen_errors": total_pre_screen_errors,
        "filter_hit_rate": (total_filtered / (total_filtered + total_scored)) if (total_filtered + total_scored) > 0 else 0.0,
        "labeled_count": labeled,
        "cost_usd": cost_usd,
    }
