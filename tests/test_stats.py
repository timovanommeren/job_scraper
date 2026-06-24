"""
Tests for feedback/stats.py — health dashboard stats + verdict logic.

Run: python -m pytest tests/test_stats.py -v
"""
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from feedback.stats import compute_last_run, compute_30d, compute_stats
from db.dedup import get_run_stats

_SCHEMA = (Path(__file__).parent.parent / "db" / "schema.sql").read_text(encoding="utf-8")

# Fixed local "now" for deterministic schedule-aware tests: 24 Jun 2026, 08:00 local
# (i.e. after the 07:00 scheduled scrape).
NOW = datetime(2026, 6, 24, 8, 0).astimezone()
CFG = {"window_days": 30, "scheduled_time": "07:00", "strong_threshold": 6}


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    # source_yields is added in production via migrations.py, not schema.sql.
    c.execute("ALTER TABLE run_log ADD COLUMN source_yields TEXT")
    return c


def _add_run(conn, *, started, finished="default", status="success",
             new=0, scored=0, filtered=0, emailed=0, api_err=0, ps_err=0,
             source_yields=None):
    if finished == "default":
        finished = started  # finished == started by default (clean, instantaneous)
    conn.execute(
        """INSERT INTO run_log
           (started_at, finished_at, sites_scraped, new_jobs_found, jobs_scored,
            jobs_filtered, jobs_emailed, api_errors, pre_screen_errors, status, source_yields)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (started.isoformat(), finished.isoformat() if finished else None,
         24, new, scored, filtered, emailed, api_err, ps_err, status, source_yields),
    )
    conn.commit()


_job_seq = 0


def _add_job(conn, *, score, source="euraxess", contract_type="PhD",
             title="PhD in stats", seen=None, deadline=None, active=1):
    global _job_seq
    _job_seq += 1
    seen = seen or NOW
    conn.execute(
        """INSERT INTO jobs (url, url_hash, source, title, contract_type,
                             deadline, relevance_score, first_seen_at, last_seen_at, is_active)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (f"https://example.com/job/{_job_seq}", f"hash-{_job_seq}",
         source, title, contract_type, deadline, score,
         seen.isoformat(), seen.isoformat(), active),
    )
    conn.commit()


# ── Verdict states ───────────────────────────────────────────────────────────

def test_no_runs_yet(conn):
    v = compute_last_run(conn, CFG, now=NOW)
    assert v["state"] == "NO_RUNS_YET"
    assert v["detail"] is None


def test_sent(conn):
    _add_run(conn, started=NOW, status="success", new=10, emailed=3)
    v = compute_last_run(conn, CFG, now=NOW)
    assert v["state"] == "SENT" and v["color"] == "green"


def test_no_email(conn):
    _add_run(conn, started=NOW, status="success", new=14, emailed=0)
    v = compute_last_run(conn, CFG, now=NOW)
    assert v["state"] == "NO_EMAIL" and v["color"] == "slate"


def test_nothing_new(conn):
    _add_run(conn, started=NOW, status="success", new=0, emailed=0)
    v = compute_last_run(conn, CFG, now=NOW)
    assert v["state"] == "NOTHING_NEW"


def test_partial(conn):
    _add_run(conn, started=NOW, status="partial", new=5, emailed=1, api_err=2)
    v = compute_last_run(conn, CFG, now=NOW)
    assert v["state"] == "PARTIAL" and v["color"] == "amber"


def test_failed_status(conn):
    _add_run(conn, started=NOW, status="failed")
    v = compute_last_run(conn, CFG, now=NOW)
    assert v["state"] == "FAILED" and v["color"] == "red"


def test_no_network(conn):
    _add_run(conn, started=NOW, status="no_network")
    v = compute_last_run(conn, CFG, now=NOW)
    assert v["state"] == "NO_NETWORK" and v["color"] == "blue"


def test_running(conn):
    # started 5 min ago, not finished → still running, not crashed
    _add_run(conn, started=NOW - timedelta(minutes=5), finished=None, status=None)
    v = compute_last_run(conn, CFG, now=NOW)
    assert v["state"] == "RUNNING"


def test_crashed(conn):
    # started 3h ago, never finished → crashed (past the 2h guard)
    _add_run(conn, started=NOW - timedelta(hours=3), finished=None, status=None)
    v = compute_last_run(conn, CFG, now=NOW)
    assert v["state"] == "CRASHED" and v["color"] == "red"


def test_next_run_before_scheduled(conn):
    # Only yesterday's run exists; "now" is 06:00 (before today's 07:00) → NEXT RUN
    early = datetime(2026, 6, 24, 6, 0).astimezone()
    _add_run(conn, started=early - timedelta(days=1), status="success", new=3, emailed=1)
    v = compute_last_run(conn, CFG, now=early)
    assert v["state"] == "NEXT_RUN"


def test_failed_missed_after_scheduled(conn):
    # Only yesterday's run; now is 08:00 (past 07:00 + grace), nothing today → FAILED
    _add_run(conn, started=NOW - timedelta(days=1), status="success", new=3, emailed=1)
    v = compute_last_run(conn, CFG, now=NOW)
    assert v["state"] == "FAILED"


# ── 30-day analytics ─────────────────────────────────────────────────────────

def test_by_type_were_they_all_phds(conn):
    _add_job(conn, score=8, contract_type="PhD position", title="PhD in methodology")
    _add_job(conn, score=7, contract_type="Traineeship", title="Blue Book trainee")
    _add_job(conn, score=6, contract_type="Research", title="Research analyst")
    _add_job(conn, score=3, contract_type="PhD", title="low score, excluded")
    a = compute_30d(conn, CFG, now=NOW)
    assert a["by_type"]["PhD position"] == 1
    assert a["by_type"]["Traineeship"] == 1
    assert a["strong_total"] == 3   # the score-3 job is below threshold


def test_score_distribution(conn):
    for s in (9, 7, 7, 5, 2, 2, 2):
        _add_job(conn, score=s)
    a = compute_30d(conn, CFG, now=NOW)
    assert a["score_distribution"] == {"8-10": 1, "6-7": 2, "4-5": 1, "1-3": 3}


def test_upcoming_deadlines(conn):
    _add_job(conn, score=7, deadline=(NOW + timedelta(days=5)).date().isoformat())
    _add_job(conn, score=7, deadline=(NOW + timedelta(days=40)).date().isoformat())  # too far
    a = compute_30d(conn, CFG, now=NOW)
    assert a["upcoming_deadlines"] == 1


def test_spend_not_instrumented_in_pr1(conn):
    a = compute_30d(conn, CFG, now=NOW)
    assert a["spend"] == {"instrumented": False}


# ── Empty DB (first run must not crash) ──────────────────────────────────────

def test_compute_stats_empty_db(conn):
    stats = compute_stats(conn, now=NOW)
    assert stats["last_run"]["state"] == "NO_RUNS_YET"
    assert stats["last_30_days"]["strong_total"] == 0
    assert stats["last_30_days"]["runs"]["total"] == 0


# ── Regression guard: get_run_stats keeps its documented shape ───────────────
# The dashboard reuses get_run_stats(); the weekly digest also depends on it.
# This guards the contract so a future extension (eng-review E4) can't silently
# break the digest.

def test_get_run_stats_contract(conn):
    _add_run(conn, started=NOW, status="success", scored=10, filtered=5)
    stats = get_run_stats(30, conn)
    for key in ("n_runs", "failed_runs", "total_scored", "total_filtered",
                "filter_hit_rate", "labeled_count", "cost_usd"):
        assert key in stats
    assert stats["n_runs"] == 1
    assert stats["total_scored"] == 10
