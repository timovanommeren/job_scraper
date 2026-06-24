"""
feedback/stats.py — Health dashboard statistics.

Pure data layer: connection in, dict out. Shared by GET /dashboard (HTML) and
GET /api/v1/stats (JSON), so the numbers are computed once and reused (this is
the whole point of the JSON endpoint — reusability).

Run-log aggregation is delegated to db.dedup.get_run_stats() so the weekly digest
and the dashboard read run_log the same way (single source of truth, DRY).

NOTE (PR split): token/cost columns are added in a later PR. Until then the
"API spend" section returns {"instrumented": False} and the dashboard shows a
placeholder instead of euro figures. compute_stats() never references the token
columns, so it is safe to run before that migration lands.

Verdict logic — "did the scrape run, and if no email, why not?"

    no run_log rows ............................. NO_RUNS_YET (slate)
    latest row, finished_at IS NULL:
        started < MAX_RUN guard ................. RUNNING    (blue)   still going
        started >= MAX_RUN guard ................ CRASHED    (red)    half-written row
    before today's scheduled time, none today .. NEXT_RUN   (slate)  not due yet
    past scheduled time + grace, none today .... FAILED     (red)    didn't fire
    latest finished run, by status:
        no_network ............................. NO_NETWORK (blue)
        failed ................................. FAILED     (red)
        partial ................................ PARTIAL    (amber)
        success, jobs_emailed > 0 .............. SENT       (green)
        success, new > 0, emailed = 0 .......... NO_EMAIL   (slate)
        success, new = 0 ....................... NOTHING_NEW(slate)

All time comparisons are in local time (started_at is UTC-stored; converted).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta, date
from pathlib import Path

import yaml

log = logging.getLogger("feedback_server")

_SETTINGS_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"

# A finished_at-less row older than this is treated as crashed, not still running.
# The daily scrape completes in minutes; 2h is a safe guard against false CRASHED.
_MAX_RUN_MINUTES = 120
# Grace after the scheduled time before a missing run is called FAILED (the task
# may start a few minutes late; log_run_start writes the row near kickoff).
_MISSED_GRACE_MINUTES = 30
# Upcoming-deadline horizon for the analytics tile.
_DEADLINE_HORIZON_DAYS = 14

# Documented scraper states (CLAUDE.md). A 0 yield from these is expected, not a
# failure, so the dashboard labels them rather than flagging them red.
_DISABLED_SOURCES = {"uncareers", "tni", "euda"}
_SEASONAL_SOURCES = {"eucareers", "epso_bluebook"}


# ── config ───────────────────────────────────────────────────────────────────

def _load_cfg() -> dict:
    try:
        cfg = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        log.warning("stats: could not read settings.yaml; using defaults", exc_info=True)
        cfg = {}
    dash = cfg.get("dashboard", {}) or {}
    return {
        "window_days": int(dash.get("analytics_window_days", 30)),
        "scheduled_time": str(dash.get("scheduled_run_time", "07:00")),
        "strong_threshold": int((cfg.get("filtering", {}) or {}).get("strong_match_threshold", 6)),
    }


# ── helpers ──────────────────────────────────────────────────────────────────

def _parse_local(s) -> datetime | None:
    """Parse an ISO timestamp (UTC-stored) and return it in local time."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone()
    except (ValueError, TypeError):
        return None


def _hhmm(dt: datetime | None) -> str:
    return dt.strftime("%H:%M") if dt else "?"


def _scheduled_today(now: datetime, scheduled_time: str) -> datetime:
    parts = (scheduled_time.split(":") + ["0", "0"])[:2]
    try:
        hh, mm = int(parts[0]), int(parts[1])
    except ValueError:
        hh, mm = 7, 0
    return now.replace(hour=hh, minute=mm, second=0, microsecond=0)


def _source_yields(row) -> list[dict]:
    raw = row["source_yields"] if "source_yields" in row.keys() else None
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    out = []
    for name, count in sorted(data.items(), key=lambda kv: (-(kv[1] or 0), kv[0])):
        key = name.lower()
        if key in _DISABLED_SOURCES:
            note = "disabled"
        elif key in _SEASONAL_SOURCES:
            note = "seasonal"
        elif (count or 0) == 0:
            note = "check"          # genuine 0 from a source that should yield
        else:
            note = ""
        out.append({"source": name, "count": count or 0, "note": note})
    return out


def _last_run_detail(row) -> dict:
    started = _parse_local(row["started_at"])
    finished = _parse_local(row["finished_at"])
    duration = None
    if started and finished:
        secs = int((finished - started).total_seconds())
        duration = f"{secs // 60}m {secs % 60}s"
    return {
        "started": started.strftime("%Y-%m-%d %H:%M:%S") if started else None,
        "duration": duration,
        "sites_scraped": row["sites_scraped"] or 0,
        "new_jobs_found": row["new_jobs_found"] or 0,
        "jobs_scored": row["jobs_scored"] or 0,
        "jobs_filtered": row["jobs_filtered"] or 0,
        "jobs_emailed": row["jobs_emailed"] or 0,
        "api_errors": row["api_errors"] or 0,
        "pre_screen_errors": row["pre_screen_errors"] or 0,
        "status": row["status"],
    }


def _classify_finished(row, started, now, cfg) -> dict:
    """Classify a row whose run has finished (or crashed)."""
    finished = _parse_local(row["finished_at"])
    t = _hhmm(started)
    if finished is None:
        age_min = (now - started).total_seconds() / 60 if started else 1e9
        if age_min < _MAX_RUN_MINUTES:
            return {"state": "RUNNING", "color": "blue",
                    "message": f"Run in progress (started {t})."}
        return {"state": "CRASHED", "color": "red",
                "message": f"Run started {t} but never finished — likely crashed or killed."}

    status = row["status"]
    emailed = row["jobs_emailed"] or 0
    new = row["new_jobs_found"] or 0
    if status == "no_network":
        return {"state": "NO_NETWORK", "color": "blue",
                "message": "Skipped — no network at run time."}
    if status == "failed":
        return {"state": "FAILED", "color": "red",
                "message": f"Last run FAILED (started {t})."}
    if status == "partial":
        return {"state": "PARTIAL", "color": "amber",
                "message": (f"Ran {t} with errors: {row['api_errors'] or 0} API, "
                            f"{row['pre_screen_errors'] or 0} pre-screen. {emailed} emailed.")}
    # success (or any unexpected status treated as a clean finish)
    if emailed > 0:
        return {"state": "SENT", "color": "green",
                "message": f"Ran {t}, finished clean. {emailed} strong "
                           f"match{'es' if emailed != 1 else ''} → email sent."}
    if new > 0:
        return {"state": "NO_EMAIL", "color": "slate",
                "message": f"Ran {t}, finished clean. {new} new "
                           f"job{'s' if new != 1 else ''}, none cleared the threshold "
                           f"({cfg['strong_threshold']}). Working as intended."}
    return {"state": "NOTHING_NEW", "color": "slate",
            "message": f"Ran {t}. Nothing new on any source today."}


# ── public: last run ─────────────────────────────────────────────────────────

def compute_last_run(conn, cfg: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now().astimezone()
    row = conn.execute(
        "SELECT * FROM run_log ORDER BY started_at DESC LIMIT 1"
    ).fetchone()

    if row is None:
        return {"state": "NO_RUNS_YET", "color": "slate",
                "message": f"No runs recorded yet. The first scrape runs daily "
                           f"at {cfg['scheduled_time']}.",
                "detail": None, "source_yields": []}

    started = _parse_local(row["started_at"])
    finished = _parse_local(row["finished_at"])
    ran_today = started is not None and started.date() == now.date()
    sched_today = _scheduled_today(now, cfg["scheduled_time"])
    detail = _last_run_detail(row)
    yields = _source_yields(row)

    # A run is still in flight / crashed regardless of the schedule check.
    if finished is None:
        return {**_classify_finished(row, started, now, cfg),
                "detail": detail, "source_yields": yields}

    # Schedule-aware "did today's run fire?" check.
    if not ran_today:
        if now < sched_today:
            return {"state": "NEXT_RUN", "color": "slate",
                    "message": f"Next scrape at {cfg['scheduled_time']}.",
                    "detail": detail, "source_yields": yields}
        if now >= sched_today + timedelta(minutes=_MISSED_GRACE_MINUTES):
            return {"state": "FAILED", "color": "red",
                    "message": f"No run since today's {cfg['scheduled_time']} — "
                               f"the scrape may not have fired.",
                    "detail": detail, "source_yields": yields}
        # In the grace window: fall through and show the last finished run.

    return {**_classify_finished(row, started, now, cfg),
            "detail": detail, "source_yields": yields}


# ── public: 30-day analytics ─────────────────────────────────────────────────

def _classify_job_type(contract_type: str | None, title: str | None) -> str:
    blob = f"{contract_type or ''} {title or ''}".lower()
    if "phd" in blob or "doctoral" in blob or "doctorate" in blob:
        return "PhD position"
    if "trainee" in blob or "internship" in blob or "intern " in blob or "stage" in blob:
        return "Traineeship"
    if "postdoc" in blob or "post-doc" in blob:
        return "Postdoc"
    return "Research / analyst / other"


def compute_30d(conn, cfg: dict, now: datetime | None = None) -> dict:
    from db.dedup import get_run_stats  # reuse run_log aggregation (DRY)

    now = now or datetime.now().astimezone()
    window = cfg["window_days"]
    threshold = cfg["strong_threshold"]
    cutoff_date = (now.date() - timedelta(days=window)).isoformat()

    run_stats = get_run_stats(window, conn)

    # Days actually covered (distinct local run dates) → missed-day signal.
    run_days = conn.execute(
        "SELECT started_at FROM run_log WHERE substr(started_at,1,10) >= ?",
        (cutoff_date,),
    ).fetchall()
    days_with_run = len({_parse_local(r["started_at"]).date()
                         for r in run_days if _parse_local(r["started_at"])})

    # Strong matches in window, by inferred job type ("were they all PhDs?").
    strong_rows = conn.execute(
        """SELECT contract_type, title FROM jobs
           WHERE relevance_score >= ? AND substr(first_seen_at,1,10) >= ?""",
        (threshold, cutoff_date),
    ).fetchall()
    by_type: dict[str, int] = {}
    for r in strong_rows:
        bucket = _classify_job_type(r["contract_type"], r["title"])
        by_type[bucket] = by_type.get(bucket, 0) + 1

    # Top sources by strong-match count.
    top_sources = conn.execute(
        """SELECT source, COUNT(*) AS n FROM jobs
           WHERE relevance_score >= ? AND substr(first_seen_at,1,10) >= ?
           GROUP BY source ORDER BY n DESC LIMIT 8""",
        (threshold, cutoff_date),
    ).fetchall()

    # Score distribution buckets.
    dist_rows = conn.execute(
        """SELECT relevance_score FROM jobs
           WHERE relevance_score IS NOT NULL AND substr(first_seen_at,1,10) >= ?""",
        (cutoff_date,),
    ).fetchall()
    buckets = {"8-10": 0, "6-7": 0, "4-5": 0, "1-3": 0}
    for r in dist_rows:
        s = r["relevance_score"]
        if s >= 8:
            buckets["8-10"] += 1
        elif s >= 6:
            buckets["6-7"] += 1
        elif s >= 4:
            buckets["4-5"] += 1
        else:
            buckets["1-3"] += 1

    # Feedback engagement.
    fb = conn.execute(
        """SELECT COUNT(*) AS n, AVG(relevance_score) AS avg FROM feedback
           WHERE substr(timestamp,1,10) >= ?""",
        (cutoff_date,),
    ).fetchone()
    views = conn.execute(
        "SELECT COUNT(*) AS n FROM job_views WHERE substr(viewed_at,1,10) >= ?",
        (cutoff_date,),
    ).fetchone()["n"]

    # Upcoming deadlines (active jobs, next N days).
    today = now.date().isoformat()
    horizon = (now.date() + timedelta(days=_DEADLINE_HORIZON_DAYS)).isoformat()
    deadlines = conn.execute(
        """SELECT COUNT(*) AS n FROM jobs
           WHERE deadline IS NOT NULL
             AND substr(deadline,1,10) >= ? AND substr(deadline,1,10) <= ?
             AND is_active = 1""",
        (today, horizon),
    ).fetchone()["n"]

    n_runs = run_stats["n_runs"]
    failed = run_stats["failed_runs"]
    return {
        "window_days": window,
        "runs": {
            "total": n_runs,
            "failed_or_partial": failed,
            "success_rate": round(100 * (n_runs - failed) / n_runs) if n_runs else None,
            "days_with_run": days_with_run,
            "missed_days": max(0, window - days_with_run),
        },
        "strong_total": len(strong_rows),
        "by_type": by_type,
        "top_sources": [{"source": r["source"], "count": r["n"]} for r in top_sources],
        "score_distribution": buckets,
        "feedback": {
            "count": fb["n"] or 0,
            "avg_rating": round(fb["avg"], 1) if fb["avg"] is not None else None,
            "views": views,
        },
        "upcoming_deadlines": deadlines,
        "labeled_count": run_stats["labeled_count"],
        # Token/cost instrumentation lands in a later PR (see design doc).
        "spend": {"instrumented": False},
    }


# ── public: entry point ──────────────────────────────────────────────────────

def compute_stats(conn, now: datetime | None = None) -> dict:
    """Full dashboard payload. Shared by /dashboard and /api/v1/stats."""
    cfg = _load_cfg()
    return {
        "last_run": compute_last_run(conn, cfg, now=now),
        "last_30_days": compute_30d(conn, cfg, now=now),
    }
