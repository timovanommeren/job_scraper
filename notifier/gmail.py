import hmac
import hashlib
import smtplib
import os
import logging
import json
import sqlite3
import time
import yaml
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import quote as urlquote

logger = logging.getLogger(__name__)

FEEDBACK_BASE = "http://localhost:5001"


def _feedback_action_url(job_id: str, action: str, score: int | None = None) -> str:
    """
    Return a signed Cloudflare Worker URL for the given like/pass/rate action.
    Falls back to the localhost Flask URL when CF_WORKER_URL is not configured.

    action values: "like", "pass", "rate"
    HMAC uses a 24-hour daily bucket so links are valid for the whole calendar day.
    """
    cf_url = os.getenv("CF_WORKER_URL", "").rstrip("/")
    secret = os.getenv("CF_WORKER_SECRET", "")
    if not cf_url or not secret:
        # Fallback: existing localhost /fb route (desktop only)
        return f"{FEEDBACK_BASE}/fb?id={job_id}&a={action}"

    day_bucket = int(time.time()) // 86400
    payload = f"{job_id}:{action}:{day_bucket}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]

    if action == "rate":
        if score is not None:
            # Rating row pill: direct score recording at /feedback (no form shown)
            return f"{cf_url}/feedback?job_id={job_id}&sig={sig}&score={score}"
        # Old "Rate" button: open slider form at /rate
        return f"{cf_url}/rate?job_id={job_id}&sig={sig}"
    return f"{cf_url}/feedback?job_id={job_id}&action={action}&sig={sig}"


def _skip_suggestion_url(suggestion_id: int) -> str:
    """Return a signed CF Worker URL for skipping a source suggestion (mobile-compatible).
    Falls back to localhost Flask route when CF is not configured."""
    cf_url = os.getenv("CF_WORKER_URL", "").rstrip("/")
    secret = os.getenv("CF_WORKER_SECRET", "")
    if not cf_url or not secret:
        return f"http://localhost:5001/skip-suggestion?id={suggestion_id}"
    day_bucket = int(time.time()) // 86400
    payload = f"{suggestion_id}:skip_suggestion:{day_bucket}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{cf_url}/feedback?suggestion_id={suggestion_id}&action=skip_suggestion&sig={sig}"


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

    jid = str(job["id"])

    deadline_line = ""
    if job["deadline"]:
        deadline_line = f'<br>⏰ {job["deadline"]} &nbsp; {dl_badge}'

    # Rating row — two rows of 5 pills (1-5 top, 6-10 bottom)
    # Color zones: 1-3 grey (pass), 4-6 amber (uncertain), 7-10 green (like)
    _pill_colors = {
        1: ("#e5e7eb", "#6b7280"), 2: ("#e5e7eb", "#6b7280"), 3: ("#e5e7eb", "#6b7280"),
        4: ("#fef3c7", "#92400e"), 5: ("#fef3c7", "#92400e"), 6: ("#fef3c7", "#92400e"),
        7: ("#dcfce7", "#166534"), 8: ("#dcfce7", "#166534"),
        9: ("#dcfce7", "#166534"), 10: ("#dcfce7", "#166534"),
    }
    _pill_style = (
        "display:inline-block;width:40px;height:44px;line-height:44px;"
        "text-align:center;border-radius:6px;font-family:Arial,sans-serif;"
        "font-size:15px;font-weight:bold;text-decoration:none;margin:3px;"
    )

    def _pill(n: int) -> str:
        bg, fg = _pill_colors[n]
        url = _feedback_action_url(jid, "rate", n)
        return f'<a href="{url}" style="{_pill_style}background:{bg};color:{fg}">{n}</a>'

    row1 = "".join(_pill(n) for n in range(1, 6))
    row2 = "".join(_pill(n) for n in range(6, 11))
    full_feedback_url = _feedback_action_url(jid, "rate")
    rating_row = (
        '<div style="margin-top:10px">'
        '<div style="font-size:11px;color:#9ca3af;margin-bottom:4px">'
        'How relevant? &nbsp;(1 = not relevant · 10 = perfect fit)</div>'
        f'<div>{row1}</div>'
        f'<div>{row2}</div>'
        f'<div style="margin-top:6px;font-size:11px">'
        f'<a href="{full_feedback_url}" style="color:#9ca3af;text-decoration:none">'
        'Full feedback (tags + note) &#8594;</a></div>'
        '</div>'
    )

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
  <div style="margin-top:8px">
    <a href="{apply_url}"
       style="background:#2563eb;color:#fff;padding:6px 14px;border-radius:4px;text-decoration:none;font-size:13px">
      → Apply
    </a>
  </div>
  {rating_row}
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

def _field_intelligence_html(recommendation, conn) -> str:
    """Build the 'Your Field This Week' HTML block for the weekly digest.
    Returns empty string when there is nothing useful to show."""
    if recommendation is None:
        return ""
    profile = recommendation.profile_summary or ""
    suggestions = recommendation.suggestions

    if not suggestions:
        if not profile or "unavailable" in profile.lower():
            return ""
        return (
            '<div style="background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;'
            'padding:16px;margin-top:24px">'
            '<h2 style="color:#0369a1;margin-top:0;font-size:16px">🌐 Your Field This Week</h2>'
            f'<p style="color:#0c4a6e;font-size:13px;margin:0">{profile}</p>'
            '</div>'
        )

    parts = []
    for s in suggestions:
        row = conn.execute(
            "SELECT id FROM source_suggestions WHERE org_name = ? AND status = 'pending' "
            "ORDER BY suggested_at DESC LIMIT 1",
            (s.name,),
        ).fetchone()
        sid = row["id"] if row else None
        careers_url = s.validated_url or s.candidate_url
        skip_html = ""
        if sid:
            skip_url = _skip_suggestion_url(sid)
            skip_html = (
                f'<a href="{skip_url}" style="font-size:11px;color:#9ca3af;'
                'text-decoration:none">Skip (don\'t suggest again) →</a>'
            )
        parts.append(
            '<div style="border:1px solid #e2e8f0;border-radius:6px;padding:12px;'
            'margin-bottom:8px;background:#fff">'
            f'<div style="font-weight:bold;font-size:14px;color:#1a1a2e">'
            f'{s.name} <span style="font-size:11px;color:#64748b;font-weight:normal">({s.country})</span></div>'
            f'<div style="color:#374151;font-size:13px;margin:4px 0">{s.description}</div>'
            '<div style="margin-top:8px;display:flex;gap:16px;flex-wrap:wrap;align-items:center">'
            f'<a href="{careers_url}" style="font-size:13px;color:#2563eb;text-decoration:none">→ View careers page</a>'
            f'{skip_html}'
            '</div>'
            '</div>'
        )

    return (
        '<div style="background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;'
        'padding:16px;margin-top:24px;margin-bottom:8px">'
        '<h2 style="color:#0369a1;margin-top:0;font-size:16px">🌐 Your Field This Week</h2>'
        f'<p style="color:#0c4a6e;font-size:13px;margin-bottom:14px">{profile}</p>'
        '<div style="font-weight:600;font-size:13px;color:#0369a1;margin-bottom:8px">'
        'Organisations worth adding as sources:</div>'
        + "".join(parts)
        + '<p style="color:#94a3b8;font-size:11px;margin:10px 0 0">'
        'Suggested by AI based on your highest-rated jobs. Adding them requires a manual code edit.</p>'
        '</div>'
    )


def _pipeline_health_html(conn, strong_thresh: int, also_thresh: int) -> str:
    """Weekly pipeline health block for the digest email."""
    try:
        from db.dedup import get_run_stats
        import yaml as _yaml
        stats = get_run_stats(7, conn)
        settings_path = Path(__file__).parent.parent / "config" / "settings.yaml"
        cfg = _yaml.safe_load(settings_path.read_text(encoding="utf-8"))
        pf_mode = cfg.get("pre_filter", {}).get("mode", "B")
    except Exception:
        logger.warning("Pipeline health block failed — skipping")
        return ""

    filter_rate_pct = f"{stats['filter_hit_rate'] * 100:.0f}%"
    cost_str = f"${stats['cost_usd']:.4f}"

    warn = ""
    if stats["total_pre_screen_errors"] > 0:
        warn = (
            f'<div style="background:#fef2f2;border:1px solid #fecaca;border-radius:4px;'
            f'padding:8px;margin-bottom:8px;color:#dc2626;font-size:12px">'
            f'⚠️ <strong>{stats["total_pre_screen_errors"]} pre-screen errors</strong> this week '
            f'— pre-filter may be disabled (failing open to full scoring). Check logs.</div>'
        )

    rows_html = "".join(
        f'<tr><td style="padding:3px 8px;color:#374151">{k}</td>'
        f'<td style="padding:3px 8px;color:#1a1a2e;font-weight:600">{v}</td></tr>'
        for k, v in [
            ("Runs (7 days)", f"{stats['n_runs']} ({stats['failed_runs']} failed/partial)"),
            ("Jobs scored", str(stats["total_scored"])),
            ("Jobs pre-filtered", str(stats["total_filtered"])),
            ("Filter hit rate", filter_rate_pct),
            ("Est. API cost (USD)", cost_str),
            ("Feedback labels", str(stats["labeled_count"])),
            ("Layer 2 mode", f"{'B — Claude pre-screen' if pf_mode == 'B' else pf_mode}"),
            ("Strong threshold", f"≥ {strong_thresh}"),
            ("Also-found threshold", f"≥ {also_thresh}"),
        ]
    )

    return (
        '<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;'
        'padding:16px;margin-top:24px">'
        '<h2 style="color:#475569;margin-top:0;font-size:14px">🔧 Pipeline Health (last 7 days)</h2>'
        + warn +
        f'<table style="font-size:12px;border-collapse:collapse">{rows_html}</table>'
        '</div>'
    )


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

    strong_thresh, also_thresh = _load_thresholds()
    strong   = [r for r in rows if (r["relevance_score"] or 0) >= strong_thresh]
    relevant = [r for r in rows if also_thresh <= (r["relevance_score"] or 0) < strong_thresh]
    low      = [r for r in rows if (r["relevance_score"] or 0) < also_thresh]

    # Run field-intelligence recommender before test_mode bail-out so it prints in test mode too.
    recommendation = None
    try:
        from feedback.source_recommender import generate_suggestions
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(generate_suggestions, None, None, test_mode)
            recommendation = future.result(timeout=90)
    except Exception:
        logger.warning("Weekly digest: source recommender timed out or failed — continuing without suggestions")

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
        print(f"Strong ({strong_thresh}-10): {len(strong)}")
        for r in strong:
            print(f"  [{r['relevance_score']}/10] {r['title']}")
        print(f"Relevant ({also_thresh}-{strong_thresh - 1}): {len(relevant)}")
        print(f"Low (1-{also_thresh - 1}): {len(low)}")
        if recommendation:
            print(f"\nField Profile: {recommendation.profile_summary}")
            if recommendation.suggestions:
                print(f"Suggestions ({len(recommendation.suggestions)}):")
                for s in recommendation.suggestions:
                    print(f"  → {s.name} ({s.country}): {s.validated_url or s.candidate_url}")
            else:
                print("Suggestions: none (not enough qualifying jobs or all URLs failed validation)")
        print(f"--- END PREVIEW ---\n")
        return len(rows)

    def _section(title_html: str, job_rows: list) -> str:
        if not job_rows:
            return ""
        cards = "\n".join(job_html(r) for r in _sort_by_deadline(job_rows))
        return f"<h2 style='margin-top:24px'>{title_html} ({len(job_rows)})</h2>\n{cards}"

    strong_sec   = _section(f"🎯 Strong Matches — {strong_thresh}–10", strong)
    relevant_sec = _section(f"💡 Relevant — {also_thresh}–{strong_thresh - 1}", relevant)
    low_sec      = _section(f"📋 Also Scraped — 1–{also_thresh - 1}", low) if low else ""
    field_intel  = _field_intelligence_html(recommendation, conn)
    health_block = _pipeline_health_html(conn, strong_thresh, also_thresh)
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
{field_intel}
{health_block}
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
