"""
main.py — Job Scraper Orchestrator
Usage:
    python main.py                       # normal daily run
    python main.py --test                # scrape + score, print digest, no DB writes, no email
    python main.py --dry-run             # scrape + score + DB write, no email
    python main.py --reprocess N         # re-score last N failed extractions
    python main.py --site <name>         # run only one scraper (for debugging)
    python main.py --weekly-digest       # send weekly digest email and exit
    python main.py --backfill-deadlines  # extract missing deadlines for existing DB jobs
"""

import argparse
import logging
import logging.handlers
import os
import socket
import sys
from datetime import date
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv(override=True)  # override=True so .env takes precedence over any empty system env vars


# ── Logging setup (must happen before any other imports that use logging) ──────

def setup_logging(level: str = "INFO") -> None:
    Path("logs").mkdir(exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        "logs/scraper.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)-20s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.addHandler(handler)
    root.addHandler(console)


logger = logging.getLogger(__name__)


# ── Network check ───────────────────────────────────────────────────────────────

def _check_network(host: str = "8.8.8.8", port: int = 53, timeout: int = 3) -> bool:
    """Returns True if a TCP connection to host:port succeeds within timeout seconds."""
    try:
        socket.setdefaulttimeout(timeout)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect((host, port))
        return True
    except OSError:
        return False


# ── Settings loader ─────────────────────────────────────────────────────────────

def load_settings() -> dict:
    settings_path = Path(__file__).parent / "config" / "settings.yaml"
    with open(settings_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ── Scraper registry ────────────────────────────────────────────────────────────

def build_scraper_registry(settings: dict) -> dict:
    """Returns a dict of {source_name: scraper_instance} for all configured scrapers."""
    from scrapers.euraxess import EuraxessScraper
    from scrapers.impactpool import ImpactpoolScraper
    from scrapers.uncareers import UnCareersScraper
    from scrapers.eucareers import EuCareersScraper
    from scrapers.oecd import OecdScraper
    from scrapers.academictransfer import AcademicTransferScraper
    from scrapers.jrc import JRCScraper
    from scrapers.rand import RandScraper
    from scrapers.tni import TNIScraper
    from scrapers.case_poland import CasePolandScraper
    from scrapers.busara import BusaraScraper
    from scrapers.wodc import WODCScraper
    from scrapers.scp import SCPScraper
    from scrapers.trimbos import TrimbosScraper
    from scrapers.bit import BITScraper
    from scrapers.fgv import FGVScraper
    from scrapers.epso_bluebook import EPSOBluebookScraper

    from scrapers.dutch_universities import create_university_scrapers

    classes = [
        EuraxessScraper,
        ImpactpoolScraper,
        UnCareersScraper,
        EuCareersScraper,
        OecdScraper,
        AcademicTransferScraper,
        JRCScraper,
        RandScraper,
        TNIScraper,
        CasePolandScraper,
        BusaraScraper,
        WODCScraper,
        SCPScraper,
        TrimbosScraper,
        BITScraper,
        FGVScraper,
        EPSOBluebookScraper,
    ]
    registry = {cls.source_name: cls(settings) for cls in classes}
    registry.update(create_university_scrapers(settings))
    return registry


# ── Core pipeline functions ─────────────────────────────────────────────────────

def run_scrapers(settings: dict, site_filter: str | None = None) -> tuple[list, dict]:
    """
    Runs all scrapers (or a single one if site_filter is set).
    Returns (aggregated RawJob list, per-source yield counts).
    Logs per-site counts; never raises — failed scrapers return [].
    """
    registry = build_scraper_registry(settings)
    all_jobs = []
    source_yields: dict[str, int] = {}

    if site_filter:
        if site_filter not in registry:
            logger.error(f"Unknown site: '{site_filter}'. Known sites: {list(registry)}")
            return [], {}
        sites = {site_filter: registry[site_filter]}
    else:
        sites = registry

    for name, scraper in sites.items():
        try:
            jobs = scraper.fetch()
            logger.info(f"{name}: fetched {len(jobs)} jobs")
            all_jobs.extend(jobs)
            source_yields[name] = len(jobs)
        except Exception:
            logger.exception(f"{name}: unexpected top-level exception")
            source_yields[name] = 0

    return all_jobs, source_yields


def score_new_jobs(raw_jobs: list, conn, client, dry_run: bool = False) -> tuple:
    """
    For each RawJob: dedup → pre_screen → extract_and_score → insert.
    Returns (new_postings, api_error_count, jobs_filtered_count, pre_screen_error_count).
    In dry_run mode, scores but does NOT insert into DB.
    """
    from db.dedup import is_seen, update_last_seen, insert_job, insert_failed, insert_filtered
    from agents.extractor_scorer import safe_extract_and_score, pre_screen

    new_postings = []
    api_errors = 0
    jobs_filtered = 0
    pre_screen_errors = 0

    for raw in raw_jobs:
        if not raw.url:
            logger.warning(f"Skipping job with empty URL (source={raw.source}, title={raw.title!r})")
            continue

        # Dedup L1+L2: explicit string comparison required — all return values are truthy strings.
        status = is_seen(raw.url, conn, title=raw.title or "", org=raw.organization or "")
        if status == "scored":
            update_last_seen(raw.url, conn)
            continue
        elif status == "filtered":
            continue  # do NOT call update_last_seen — job is not in jobs table

        # Layer 2: cheap field-check before full LLM extraction.
        passed, reason = pre_screen(raw, client)
        if reason == "pre_screen_error":
            pre_screen_errors += 1
        if not passed:
            jobs_filtered += 1
            if not dry_run:
                insert_filtered(raw, "pre_screen", reason, conn)
            continue

        posting = safe_extract_and_score(raw, client)
        if posting is None:
            api_errors += 1
            if not dry_run:
                insert_failed(raw, "safe_extract_and_score returned None", conn)
            continue

        if not dry_run:
            insert_job(raw, posting, conn)
        new_postings.append((raw, posting))

    return new_postings, api_errors, jobs_filtered, pre_screen_errors


def send_digest(new_postings: list, stats, conn, test_mode: bool = False) -> int:
    """
    Filters jobs by score tier and sends daily email if strong matches exist.
    Daily email:  strong ≥ 8, also-found 6–7.  Jobs ≤ 5 are DB-only.
    Returns count of emailed jobs (0 if no strong matches or test mode).
    """
    from db.dedup import mark_emailed
    from notifier.gmail import (
        send_digest as _send, save_fallback_html, RunStats,
        should_send_daily, STRONG_THRESHOLD, ALSO_THRESHOLD,
    )

    run_stats = RunStats(
        sites_scraped=stats["sites_scraped"],
        new_jobs_found=stats["new_jobs_found"],
        jobs_scored=stats["jobs_scored"],
        api_errors=stats["api_errors"],
        run_date=str(date.today()),
    )

    if test_mode:
        strong = [(r, p) for r, p in new_postings if p.relevance_score >= STRONG_THRESHOLD]
        also   = [(r, p) for r, p in new_postings if ALSO_THRESHOLD <= p.relevance_score < STRONG_THRESHOLD]
        print("\n--- DIGEST PREVIEW ---")
        print(f"Strong (>={STRONG_THRESHOLD}) - {len(strong)} jobs:")
        for _, p in strong:
            print(f"  [{p.relevance_score}/10] {p.title} | {p.organization}")
        print(f"Also found (6-7) - {len(also)} jobs:")
        for _, p in also:
            print(f"  [{p.relevance_score}/10] {p.title} | {p.organization}")
        would_send = should_send_daily(new_postings)
        print(f"Would send daily email: {would_send}")
        print("--- END PREVIEW ---\n")
        logger.info("Test mode: no email sent, no DB writes")
        return 0

    # Check cadence: only send if at least one score >= 8
    if not should_send_daily(new_postings):
        max_score = max((p.relevance_score for _, p in new_postings), default=0)
        logger.info(
            f"[NOTIFIER] No strong matches today (max score: {max_score}). "
            f"Skipping daily email. Jobs available at http://localhost:5001/jobs"
        )
        return 0

    # Re-query DB rows (sqlite3.Row with all columns including id)
    strong_rows  = []
    also_rows    = []
    emailed_ids  = []

    for raw, posting in new_postings:
        row = conn.execute("SELECT * FROM jobs WHERE url = ?", (raw.url,)).fetchone()
        if row is None:
            continue
        score = posting.relevance_score
        if score >= STRONG_THRESHOLD:
            strong_rows.append(row)
            emailed_ids.append(row["id"])
        elif score >= ALSO_THRESHOLD:
            also_rows.append(row)
            emailed_ids.append(row["id"])

    try:
        _send(strong_rows, also_rows, run_stats)
        if emailed_ids:
            mark_emailed(emailed_ids, conn)
        return len(emailed_ids)
    except Exception:
        logger.exception("Failed to send email digest; saving fallback HTML")
        save_fallback_html(strong_rows, also_rows, run_stats)
        return 0


def reprocess_failed(n: int, conn, client) -> None:
    """Re-score the last N unretried failed extractions."""
    from db.dedup import get_failed_extractions, insert_job, mark_failed_retried
    from agents.extractor_scorer import safe_extract_and_score
    from scrapers.base import RawJob

    rows = get_failed_extractions(n, conn)
    logger.info(f"Reprocessing {len(rows)} failed extractions")
    for row in rows:
        raw = RawJob(
            title="",
            url=row["url"],
            source=row["source"] or "unknown",
            raw_text=row["raw_text"] or "",
        )
        posting = safe_extract_and_score(raw, client)
        mark_failed_retried(row["id"], conn)
        if posting is not None:
            try:
                insert_job(raw, posting, conn)
                logger.info(f"Reprocessed successfully: {raw.url}")
            except Exception:
                logger.exception(f"Failed to insert reprocessed job: {raw.url}")


# ── Entry point ─────────────────────────────────────────────────────────────────

def main(
    test_mode: bool = False,
    dry_run: bool = False,
    reprocess: int = 0,
    site: str | None = None,
    weekly_digest: bool = False,
    backfill_deadlines: bool = False,
) -> None:
    settings = load_settings()
    setup_logging(settings.get("logging", {}).get("level", "INFO"))

    # Skip the network check for single-site runs and one-shot modes — those are
    # typically run manually and the operator can see the error immediately.
    full_pipeline = not any([test_mode, dry_run, site, weekly_digest, backfill_deadlines, reprocess])
    if full_pipeline and not _check_network():
        logger.warning(
            "No network connectivity detected — aborting run (exit 1). "
            "If JobScraperDaily is configured with retry-on-failure, it will retry in 60 min."
        )
        sys.exit(1)

    from db.migrations import init_db
    from db.dedup import get_connection, log_run_start, log_run_finish
    from agents.extractor_scorer import build_client

    init_db()
    conn = get_connection()
    client = build_client()

    # ── One-shot modes (exit after completion) ──────────────────────────────
    if weekly_digest:
        from notifier.gmail import send_weekly_digest
        count = send_weekly_digest(conn, test_mode=test_mode)
        logger.info(f"Weekly digest complete: {count} jobs included")
        conn.close()
        return

    if backfill_deadlines:
        from agents.extractor_scorer import backfill_deadlines as _backfill
        filled = _backfill(conn, client)
        logger.info(f"Deadline backfill complete: {filled} deadlines filled")
        conn.close()
        return

    run_id = log_run_start(conn)
    api_errors = 0
    new_count = 0
    scored_count = 0
    filtered_count = 0
    pre_screen_err = 0
    emailed_count = 0
    source_yields: dict = {}
    status = "success"

    try:
        if reprocess > 0:
            reprocess_failed(reprocess, conn, client)
            return

        logger.info(
            f"Starting run (test_mode={test_mode}, dry_run={dry_run}, site={site or 'all'})"
        )

        # Sync any phone feedback from Cloudflare KV before scoring.
        # Wrapped in try/except so a CF outage never aborts the daily scrape.
        try:
            from feedback.cf_sync import sync_pending_feedback
            sync_pending_feedback()
        except Exception:
            logger.warning("[cf_sync] Startup sync failed — continuing without it")

        raw_jobs, source_yields = run_scrapers(settings, site_filter=site)
        sites_scraped = len(set(j.source for j in raw_jobs))
        logger.info(f"Total raw jobs fetched: {len(raw_jobs)} from {sites_scraped} sources")

        new_postings, api_errors, filtered_count, pre_screen_err = score_new_jobs(
            raw_jobs, conn, client, dry_run=test_mode or dry_run
        )
        scored_count = len(new_postings)
        new_count = scored_count + api_errors
        logger.info(
            f"New jobs: {scored_count} scored, {filtered_count} pre-filtered, {api_errors} API errors"
            + (f", {pre_screen_err} pre-screen failures (failing open)" if pre_screen_err else "")
        )

        stats = {
            "sites_scraped": sites_scraped,
            "new_jobs_found": new_count,
            "jobs_scored": scored_count,
            "jobs_filtered": filtered_count,
            "api_errors": api_errors,
            "pre_screen_errors": pre_screen_err,
        }

        if not dry_run:
            emailed_count = send_digest(new_postings, stats, conn, test_mode=test_mode)
            logger.info(f"Emailed: {emailed_count} jobs")

        if api_errors > 0:
            status = "partial"

    except Exception:
        logger.exception("Fatal error in main()")
        status = "failed"
    finally:
        log_run_finish(
            run_id=run_id,
            sites_scraped=len(set(j.source for j in raw_jobs)) if "raw_jobs" in dir() else 0,
            new_jobs_found=new_count,
            jobs_scored=scored_count,
            jobs_filtered=filtered_count if "filtered_count" in dir() else 0,
            jobs_emailed=emailed_count,
            api_errors=api_errors,
            pre_screen_errors=pre_screen_err if "pre_screen_err" in dir() else 0,
            status=status,
            source_yields=source_yields,
            conn=conn,
        )
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Job Scraper — daily digest for Timo")
    parser.add_argument("--test",    action="store_true", help="Print digest to stdout; no DB writes, no email")
    parser.add_argument("--dry-run", action="store_true", help="Write to DB but do not send email")
    parser.add_argument("--reprocess", type=int, default=0, metavar="N", help="Re-score last N failed extractions")
    parser.add_argument("--site",    type=str, default=None, help="Run only one scraper (e.g. --site euraxess)")
    parser.add_argument("--weekly-digest",      action="store_true",
                        help="Send weekly digest email for the last 7 days and exit")
    parser.add_argument("--backfill-deadlines", action="store_true",
                        help="Extract missing deadlines for all existing DB jobs and exit")
    args = parser.parse_args()

    main(
        test_mode=args.test,
        dry_run=args.dry_run,
        reprocess=args.reprocess,
        site=args.site,
        weekly_digest=args.weekly_digest,
        backfill_deadlines=args.backfill_deadlines,
    )
