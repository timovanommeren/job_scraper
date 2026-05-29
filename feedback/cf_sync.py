"""
feedback/cf_sync.py — Sync phone feedback from Cloudflare KV to local DB + JSON store.

Runs hourly via Windows Task Scheduler (JobScraperFeedbackSync).
Also called at scraper startup (main.py) to capture overnight phone feedback
before the 07:00 scoring run.

Flow:
  1. GET {CF_WORKER_URL}/poll with Authorization header → pending KV entries
  2. For each entry: look up job in SQLite, write to feedback_store.json +
     SQLite feedback table
  3. DELETE {CF_WORKER_URL}/poll → clear synced entries from KV

Score mapping for quick like/pass buttons (no slider):
  like → 8, pass → 2  (matches the floor/ceiling of the scoring convention)
  /rate form submissions carry the actual numeric score from the slider.

Requires in .env:
  CF_WORKER_URL      — https://<worker>.workers.dev
  CF_WORKER_SECRET   — shared HMAC + poll auth secret
"""

import json
import logging
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# Make sure project root is on sys.path when run directly
sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)

ACTION_TO_SCORE = {"like": 8, "pass": 2}


def _write_feedback_sqlite(job_id: str, relevance_score: int) -> None:
    """Upsert a minimal feedback row into the SQLite feedback table."""
    from db.dedup import get_connection
    conn = get_connection()
    try:
        conn.execute("DELETE FROM feedback WHERE job_id = ?", (job_id,))
        conn.execute(
            "INSERT INTO feedback (job_id, relevance_score) VALUES (?, ?)",
            (job_id, relevance_score),
        )
        conn.commit()
    except Exception:
        logger.exception(f"[cf_sync] SQLite write failed for job_id={job_id}")
    finally:
        conn.close()


def _lookup_job(job_id: str) -> dict | None:
    """Return job metadata dict from SQLite, or None if not found."""
    from db.dedup import get_connection
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT url, title, organization, relevance_score FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "url": row["url"] or "",
            "title": row["title"] or "",
            "organization": row["organization"] or "",
            "relevance_score": row["relevance_score"] or 5,
        }
    finally:
        conn.close()


_HEADERS = {
    "Authorization": "",  # filled per-call
    "User-Agent": "job-scraper-sync/1.0",
    "Accept": "application/json",
}


def _poll(worker_url: str, secret: str) -> list:
    """GET /poll → list of pending feedback entries."""
    req = Request(
        f"{worker_url}/poll",
        headers={**_HEADERS, "Authorization": f"Bearer {secret}"},
    )
    try:
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        logger.error(f"[cf_sync] Poll failed with HTTP {e.code}: {e.reason}")
    except URLError as e:
        logger.error(f"[cf_sync] Poll network error: {e.reason}")
    except Exception:
        logger.exception("[cf_sync] Poll unexpected error")
    return []


def _clear(worker_url: str, secret: str) -> int:
    """DELETE /poll → clear all synced KV entries. Returns count deleted."""
    req = Request(
        f"{worker_url}/poll",
        method="DELETE",
        headers={**_HEADERS, "Authorization": f"Bearer {secret}"},
    )
    try:
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            return data.get("deleted", 0)
    except Exception:
        logger.exception("[cf_sync] Clear KV failed")
    return 0


def sync_pending_feedback() -> int:
    """
    Pull phone feedback from Cloudflare KV and write it to local storage.
    Returns the number of entries successfully synced.
    Logs errors but never raises — caller must not crash on CF outage.
    """
    worker_url = os.getenv("CF_WORKER_URL", "").rstrip("/")
    secret = os.getenv("CF_WORKER_SECRET", "")

    if not worker_url or not secret:
        logger.debug("[cf_sync] CF_WORKER_URL or CF_WORKER_SECRET not set — skipping sync")
        return 0

    try:
        entries = _poll(worker_url, secret)
    except Exception:
        logger.exception("[cf_sync] Poll raised unexpectedly — skipping sync")
        return 0

    if not entries:
        logger.debug("[cf_sync] No pending phone feedback")
        return 0

    logger.info(f"[cf_sync] {len(entries)} pending phone feedback entries")
    synced = 0

    for entry in entries:
        job_id = str(entry.get("job_id", "")).strip()
        action = entry.get("action", "pass")
        score_raw = entry.get("score")  # None for quick like/pass, int for /rate form
        reason = entry.get("reason", "")

        if not job_id:
            logger.warning("[cf_sync] Entry with empty job_id — skipping")
            continue

        # Numeric score: use slider value if present, else map action → score
        if score_raw is not None:
            try:
                score = max(1, min(10, int(score_raw)))
            except (ValueError, TypeError):
                score = ACTION_TO_SCORE.get(action, 5)
        else:
            score = ACTION_TO_SCORE.get(action, 5)

        # Look up job metadata from SQLite
        job = _lookup_job(job_id)
        if job is None:
            logger.warning(f"[cf_sync] job_id={job_id} not found in DB — skipping")
            continue

        # Write to feedback_store.json (used by profile_updater for prompt calibration)
        try:
            from feedback.store import add_feedback
            add_feedback(
                job_id=job_id,
                url=job["url"],
                title=job["title"],
                organization=job["organization"],
                score=score,
                action=action,
                comment=reason,
            )
        except Exception:
            logger.exception(f"[cf_sync] feedback_store write failed for job_id={job_id}")
            continue

        # Write to SQLite feedback table (for Flask /feedback history page)
        _write_feedback_sqlite(job_id, score)

        logger.info(f"[cf_sync] Synced job_id={job_id} action={action} score={score}")
        synced += 1

    # Clear all KV entries regardless of individual success/failure
    # (unprocessable entries — bad job_ids — would accumulate otherwise)
    deleted = _clear(worker_url, secret)
    logger.info(f"[cf_sync] Sync complete: {synced}/{len(entries)} synced, {deleted} KV entries cleared")
    return synced


if __name__ == "__main__":
    import logging.handlers
    from dotenv import load_dotenv
    load_dotenv(override=True)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    n = sync_pending_feedback()
    print(f"Synced {n} feedback entries.")
