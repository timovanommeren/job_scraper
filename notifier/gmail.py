import smtplib
import os
import logging
import json
import sqlite3
import yaml
from dataclasses import dataclass
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import quote as urlquote

logger = logging.getLogger(__name__)

FEEDBACK_BASE = "http://localhost:5001"


def _load_thresholds() -> tuple[int, int]:
    """Load score thresholds from config/settings.yaml.

    Returns (strong_match_threshold, email_also_min_score).
    Falls back to (8, 6) if settings.yaml is missing or malformed.
    """
    try:
        settings_path = Path(__file__).parent.parent / "config" / "settings.yaml"
        with open(settings_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        filt = cfg.get("filtering", {})
        strong = int(filt.get("strong_match_threshold", 8))
        also   = int(filt.get("email_also_min_score", 6))
        return strong, also
    except Exception:
        logger.warning("Could not load settings.yaml thresholds; using defaults (8, 6)")
        return 8, 6


# Score thresholds — loaded from config/settings.yaml at import time.
# To change: edit config/settings.yaml (strong_match_threshold, email_also_min_score).
STRONG_THRESHOLD, ALSO_THRESHOLD = _load_thresholds()
WEEKLY_LOW_MIN = 1   # weekly digest includes everything ≥ 1


@dataclass
class RunStats:
    sites_scraped: int
    new_jobs_found: int
    jobs_scored: int
    api_errors: int
    run_date: str


# ── Helpers ────────────────────────────────────────────────────────────────────

def should_send_daily(new_postings: list) -> bool:
    """Return True only if at least one job in new_postings has score >= 8."""
    return any(posting.relevance_score >= STRONG_THRESHOLD
               for _, posting in new_postings)


def _feedback_footer_html() -> str:
    try:
        from feedback.profile_updater import build_feedback_footer_html
        return build_feedback_footer_html()
    except Exception:
        return ""


def _deadline_badge_email(deadline_str) -> str:
    """Inline deadline badge for HTML email (plain-text fallback friendly)."""
    if not deadline_str:
        return ""
    try:
        dl   = date.fromisoformat(str(deadline_str)[:10])
        diff = (dl - date.today()).days
        if diff < 0:
            return (f'<span style="color:#9ca3af;text-decoration:line-through;font-size:11px">'
                    f'Closed {deadline_str}</span>')
        elif diff <= 14:
            return (f'<span style="background:#fef2f2;color:#dc2626;padding:1px 6px;'
                    f'border-radius:8px;font-size:11px;border:1px solid #fecaca">'
                    f'🔴 Closes in {diff} day{"s" if diff != 1 else ""}</span>')
        elif diff <= 30:
            return (f'<span style="background:#fffbeb;color:#d97706;padding:1px 6px;'
                    f'border-radius:8px;font-size:11px;border:1px solid #fde68a">'
                    f'🟡 Closes in {diff} days</span>')
        else:
            return (f'<span style="background:#f1f5f9;color:#64748b;padding:1px 6px;'
                    f'border-radius:8px;font-size:11px;border:1px solid #e2e8f0">'
                    f'⚪ {deadline_str}</span>')
    except (ValueError, TypeError):
        return ""


def _sort_by_deadline(rows: list) -> list:
    """Sort a list of sqlite3.Row objects: deadline ASC, nulls last."""
    def key(r):
        dl = r["deadline"]
        if not dl:
            return (1, "9999-99-99")
        return (0, str(dl)[:10])
    return sorted(rows, key=key)


def _smtp_send(subject: str, html_body: str) -> None:
    sender    = os.environ["GMAIL_ADDRESS"]
    password  = os.environ["GMAIL_APP_PASSWORD"]
    recipient = os.environ.get("NOTIFY_RECIPIENT", sender)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = sender
    msg["To"]      = recipient
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, password)
        server.sendmail(sender, [recipient], msg.as_string())
    logger.info(f"Email sent: {subject}")


# ── Job card HTML (used in both daily and weekly emails) ──────────────────────

def job_html(job: sqlite3.Row) -> str:
    tags = json.loads(job["tags"] or "[]")
    tag_badges = " ".join(
        f'<span style="background:#e8f4f8;padding:2px 6px;border-radius:3px;'
        f'font-size:11px;margin-right:3px">{t}</span>'
        for t in tags
    )
    score     = job["relevance_score"] or 0
    score_bar = "●" * score + "○" * (10 - score)
    apply_url = job["url"]

    dl_badge = _deadline_badge_email(job["deadline"])

    # Feedback buttons (link to job detail page)
    jid    = str(job["id"])
    jurl   = urlquote(job["url"] or "", safe="")
    jtitle = urlquote(job["title"] or "", safe="")
    jorg   = urlquote(job["organization"] or "", safe="")

    fb_like = f"{FEEDBACK_BASE}/fb?id={jid}&url={jurl}&t={jtitle}&o={jorg}&s={score}&a=like"
    fb_pass = f"{FEEDBACK_BASE}/fb?id={jid}&url={jurl}&t={jtitle}&o={jorg}&s={score}&a=pass"

    deadline_line = ""
    if job["deadline"]:
        deadline_line = f'<br>⏰ {job["deadline"]} &nbsp; {dl_badge}'

    return f"""
<div style="border:1px solid #ddd;border-radius:6px;padding:14px;margin-bottom:12px;font-family:Arial,sans-serif">
  <div style="font-size:15px;font-weight:bold;color:#1a1a2e;margin-bottom:2px">
    {job['title']}
  </div>
  <div style="color:#555;font-size:13px;margin:4px 0">
    🏢 {job['organization'] or '—'} &nbsp;|&nbsp;
    📍 {job['location'] or '—'} &nbsp;|&nbsp;
    <span style="color:#888;font-size:11px">via {job['source']}</span>
    {deadline_line}
  </div>
  <div style="color:#333;font-size:13px;margin:8px 0;font-style:italic">
    {job['description_snippet'] or ''}
  </div>
  <div style="font-size:12px;color:#666;margin:4px 0">
    Score: {score}/10 <span style="font-family:monospace">{score_bar}</span><br>
    <em>{job['relevance_reason'] or ''}</em>
  </div>
  <div style="margin:6px 0">{tag_badges}</div>
  <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px;align-items:center">
    <a href="{apply_url}"
       style="background:#2563eb;color:#fff;padding:6px 14px;border-radius:4px;text-decoration:none;font-size:13px">
      → Apply
    </a>
    <a href="{fb_like}"
       style="background:#dcfce7;color:#16a34a;padding:5px 11px;border-radius:4px;text-decoration:none;font-size:12px;border:1px solid #bbf7d0">
      ✅ Interested
    </a>
    <a href="{fb_pass}"
       style="background:#fef2f2;color:#dc2626;padding:5px 11px;border-radius:4px;text-decoration:none;font-size:12px;border:1px solid #fecaca">
      ❌ Pass
    </a>
    <a href="{FEEDBACK_BASE}/jobs/{jid}"
       style="color:#94a3b8;padding:5px 11px;border-radius:4px;text-decoration:none;font-size:12px;border:1px solid #e2e8f0">
      💬 Rate
    </a>
  </div>
</div>"""


# ── Daily email ────────────────────────────────────────────────────────────────

def build_daily_html(strong_rows: list, also_rows: list, stats: RunStats) -> str:
    """
    strong_rows: score >= 8
    also_rows:   score 6–7
    Jobs below 6 are excluded from email (available in Flask app only).
    """
    warn_banner = ""
    if stats.api_errors > 0:
        warn_banner = (
            f'<div style="background:#fff3cd;border:1px solid #ffc107;padding:10px;'
            f'border-radius:4px;margin-bottom:16px">⚠️ <strong>{stats.api_errors} jobs '
            f'could not be scored</strong> due to API errors. '
            f'Check <code>logs/scraper.log</code>.</div>'
        )

    strong_html = (
        "\n".join(job_html(j) for j in _sort_by_deadline(strong_rows))
        if strong_rows
        else "<p style='color:#888'>No strong matches today.</p>"
    )

    also_html = ""
    if also_rows:
        cards = "\n".join(job_html(j) for j in _sort_by_deadline(also_rows))
        also_html = f"""
<h2 style="color:#64748b;margin-top:24px">📋 Also Found — Score 6–7 ({len(also_rows)})</h2>
<p style="color:#94a3b8;font-size:13px;margin-bottom:12px">
  Interesting but not top matches. Full list at
  <a href="{FEEDBACK_BASE}/jobs?tier=maybe" style="color:#2563eb">{FEEDBACK_BASE}/jobs</a>
</p>
{cards}"""

    feedback_footer = _feedback_footer_html()

    return f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;max-width:700px;margin:auto;padding:20px">
<h1 style="color:#1a1a2e;border-bottom:2px solid #2563eb;padding-bottom:8px">
  🎯 Job Digest — {stats.run_date}
</h1>
{warn_banner}
<h2 style="color:#16a34a">🎯 Strong Matches — Score ≥ 8 ({len(strong_rows)})</h2>
{strong_html}
{also_html}
<hr style="margin:24px 0">
<p style="color:#888;font-size:12px">
  📊 {stats.sites_scraped} sites scraped &nbsp;|&nbsp;
  {stats.new_jobs_found} new jobs found &nbsp;|&nbsp;
  {stats.jobs_scored} scored &nbsp;|&nbsp;
  {stats.api_errors} API errors<br>
  Jobs below 6 available at
  <a href="{FEEDBACK_BASE}/jobs" style="color:#2563eb">{FEEDBACK_BASE}/jobs</a>
</p>
{feedback_footer}
</body></html>"""


def send_digest(strong_rows: list, also_rows: list, stats: RunStats) -> None:
    """Send daily digest email. Raises on SMTP failure."""
    n_strong = len(strong_rows)
    n_also   = len(also_rows)
    if n_strong == 0 and n_also == 0:
        subject = f"[Job Digest] No Strong Matches – {stats.run_date}"
    else:
        parts = []
        if n_strong:
            parts.append(f"{n_strong} Strong Match{'es' if n_strong > 1 else ''}")
        if n_also:
            parts.append(f"{n_also} Also Found")
        subject = f"[Job Digest] {', '.join(parts)} – {stats.run_date}"
    _smtp_send(subject, build_daily_html(strong_rows, also_rows, stats))


def save_fallback_html(strong_rows: list, also_rows: list, stats: RunStats) -> Path:
    """Save digest as HTML file in logs/ when SMTP fails."""
    path = Path("logs") / f"digest_fallback_{stats.run_date}.html"
    path.parent.mkdir(exist_ok=True)
    path.write_text(build_daily_html(strong_rows, also_rows, stats), encoding="utf-8")
    logger.warning(f"Email failed; digest saved to {path}")
    return path


# ── Weekly digest ──────────────────────────────────────────────────────────────

def send_weekly_digest(conn, test_mode: bool = False) -> int:
    """
    Query all jobs added in the last 7 days, send weekly digest email.
    Returns the count of jobs included.
    """
    today     = date.today()
    week_ago  = today - timedelta(days=7)
    date_from = week_ago.isoformat()

    rows = conn.execute(
        """SELECT * FROM jobs
           WHERE first_seen_at >= ?
             AND relevance_score IS NOT NULL
           ORDER BY relevance_score DESC,
                    CASE WHEN deadline IS NULL THEN 1 ELSE 0 END,
                    deadline ASC""",
        (date_from,),
    ).fetchall()

    if not rows:
        logger.info("[NOTIFIER] Weekly digest: no jobs found in last 7 days. Skipping.")
        return 0

    strong  = [r for r in rows if (r["relevance_score"] or 0) >= 8]
    relevant = [r for r in rows if 5 <= (r["relevance_score"] or 0) <= 7]
    low      = [r for r in rows if (r["relevance_score"] or 0) <= 4]

    # Check for urgent deadlines in subject
    urgent = [
        r for r in rows
        if r["deadline"] and
        0 <= (date.fromisoformat(str(r["deadline"])[:10]) - today).days <= 7
    ]
    urgent_count = len(urgent)

    date_range = f"{week_ago.strftime('%b %#d')}–{today.strftime('%#d, %Y')}"
    subject = f"Weekly Job Digest — {date_range}"
    if urgent_count:
        subject += f" ⚠️ {urgent_count} closing this week"

    if test_mode:
        print(f"\n--- WEEKLY DIGEST PREVIEW ({date_range}) ---")
        print(f"Strong (8-10): {len(strong)}")
        for r in strong:
            print(f"  [{r['relevance_score']}/10] {r['title']}")
        print(f"Relevant (5-7): {len(relevant)}")
        print(f"Low (1-4): {len(low)}")
        print(f"--- END PREVIEW ---\n")
        return len(rows)

    def _section(title_html: str, job_rows: list) -> str:
        if not job_rows:
            return ""
        cards = "\n".join(job_html(r) for r in _sort_by_deadline(job_rows))
        return f"<h2 style='margin-top:24px'>{title_html} ({len(job_rows)})</h2>\n{cards}"

    strong_sec   = _section("🎯 Strong Matches — 8–10", strong)
    relevant_sec = _section("💡 Relevant — 5–7",        relevant)
    low_sec      = _section("📋 Also Scraped — 1–4",    low) if low else ""

    feedback_footer = _feedback_footer_html()

    html = f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;max-width:700px;margin:auto;padding:20px">
<h1 style="color:#1a1a2e;border-bottom:2px solid #2563eb;padding-bottom:8px">
  📅 Weekly Job Digest — {date_range}
</h1>
<p style="background:#f1f5f9;padding:12px;border-radius:6px;font-size:14px;margin-bottom:20px">
  <strong>{len(rows)} new jobs</strong> found this week.
  {f'<strong style="color:#16a34a">{len(strong)} strong matches.</strong>' if strong else 'No strong matches this week.'}
  {f'<span style="color:#dc2626">⚠️ {urgent_count} closing within 7 days.</span>' if urgent_count else ''}
</p>
{strong_sec}
{relevant_sec}
{low_sec}
<hr style="margin:24px 0">
<p style="color:#888;font-size:12px">
  View all jobs and give feedback at
  <a href="{FEEDBACK_BASE}/jobs" style="color:#2563eb">{FEEDBACK_BASE}/jobs</a>
</p>
{feedback_footer}
</body></html>"""

    try:
        _smtp_send(subject, html)
    except Exception:
        logger.exception("Weekly digest SMTP failed; saving fallback")
        path = Path("logs") / f"weekly_fallback_{today.isoformat()}.html"
        path.parent.mkdir(exist_ok=True)
        path.write_text(html, encoding="utf-8")
        logger.warning(f"Weekly digest saved to {path}")
    return len(rows)
