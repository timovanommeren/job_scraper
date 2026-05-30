"""
feedback/server.py — Job browser + structured feedback Flask app.
http://localhost:5001

Routes
------
GET  /                       → redirect to /jobs
GET  /jobs                   → paginated job list with tier filter + deadline badges
GET  /jobs/<id>              → job detail with rich feedback form
POST /jobs/<id>/feedback     → submit structured feedback → SQLite + JSON store
GET  /feedback               → paginated feedback history
GET  /fb                     → email-button compat: redirect to /jobs/<id>
GET  /comment_form           → legacy comment form (backward compat)
POST /comment                → legacy comment save (backward compat)
GET  /health                 → health check
"""
import os
import sys
import json
import logging
import logging.handlers
from datetime import date
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).parent.parent))

from flask import Flask, request, render_template_string, redirect, url_for, g

app = Flask(__name__)
log = logging.getLogger("feedback_server")

# Unified tag vocabulary — used in feedback form and stored in SQLite + feedback_store.json
PASS_TAGS  = ["Wrong field", "Too senior/junior", "Wrong location", "Postdoc",
               "Too quantitative", "Too qualitative"]
LIKE_TAGS  = ["Great org", "Interesting topic", "Good methods fit",
               "Paid traineeship", "Policy relevance"]
ALL_TAGS   = PASS_TAGS + LIKE_TAGS
ALLOWED_TAGS = set(ALL_TAGS)

PAGE_SIZE = 20


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _get_db():
    if "db" not in g:
        from db.dedup import get_connection
        g.db = get_connection()
    return g.db


def _log_view(job_id: int) -> None:
    try:
        from db.dedup import log_view
        log_view(job_id, _get_db())
    except Exception:
        log.warning(f"log_view failed for job_id={job_id}", exc_info=True)


@app.teardown_appcontext
def _close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _write_feedback_sqlite(job_id: str, relevance_score: int,
                            tags: list, comment: str) -> None:
    """Upsert into the SQLite feedback table."""
    conn = _get_db()
    try:
        conn.execute("DELETE FROM feedback WHERE job_id = ?", (job_id,))
        conn.execute(
            """INSERT INTO feedback (job_id, relevance_score, tags, comment)
               VALUES (?, ?, ?, ?)""",
            (job_id, relevance_score,
             json.dumps(tags) if tags else None,
             comment or None),
        )
        conn.commit()
    except Exception:
        log.exception(f"SQLite feedback write failed for job_id={job_id}")


def _write_feedback_json(job_id: str, url: str, title: str, org: str,
                          score: int, action: str, comment: str,
                          tags: list | None = None) -> None:
    """Keep the JSON store up-to-date (used by profile_updater for prompt additions)."""
    try:
        from feedback.store import add_feedback
        add_feedback(job_id, url, title, org, score, action, comment, tags=tags or [])
    except Exception:
        log.exception("JSON feedback store write failed")


# ── Deadline badge ─────────────────────────────────────────────────────────────

def _deadline_badge(deadline_str) -> str:
    if not deadline_str:
        return ""
    try:
        dl = date.fromisoformat(str(deadline_str)[:10])
        diff = (dl - date.today()).days
        if diff < 0:
            return (f'<span style="color:#9ca3af;text-decoration:line-through;font-size:12px">'
                    f'Closed {deadline_str}</span>')
        elif diff <= 14:
            return (f'<span style="background:#fef2f2;color:#dc2626;padding:2px 8px;'
                    f'border-radius:10px;font-size:12px;border:1px solid #fecaca">'
                    f'🔴 Closes in {diff} day{"s" if diff != 1 else ""}</span>')
        elif diff <= 30:
            return (f'<span style="background:#fffbeb;color:#d97706;padding:2px 8px;'
                    f'border-radius:10px;font-size:12px;border:1px solid #fde68a">'
                    f'🟡 Closes in {diff} days</span>')
        else:
            return (f'<span style="background:#f1f5f9;color:#64748b;padding:2px 8px;'
                    f'border-radius:10px;font-size:12px;border:1px solid #e2e8f0">'
                    f'⚪ Deadline: {deadline_str}</span>')
    except (ValueError, TypeError):
        return ""


# ── Shared HTML pieces ─────────────────────────────────────────────────────────

_CSS = """
<style>
*{box-sizing:border-box}
body{font-family:Arial,sans-serif;margin:0;background:#f8fafc;color:#1e293b}
nav{background:#1a1a2e;padding:12px 20px;display:flex;gap:20px;align-items:center}
nav a{color:#93c5fd;text-decoration:none;font-size:14px}
nav a:hover{color:#fff}
nav .brand{font-weight:bold;font-size:15px;color:#fff;margin-right:8px}
.wrap{max-width:940px;margin:24px auto;padding:0 16px}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:16px;margin-bottom:10px}
.card-title{font-size:15px;font-weight:bold;color:#1a1a2e;margin:0 0 4px}
.card-meta{color:#64748b;font-size:13px;margin:0 0 8px}
.snippet{color:#374151;font-size:13px;font-style:italic;margin:6px 0}
.reason{color:#64748b;font-size:12px;margin:4px 0}
.tag{display:inline-block;background:#e8f4f8;padding:1px 6px;border-radius:3px;font-size:11px;margin-right:3px}
.score-bar{font-family:monospace;font-size:12px;color:#64748b}
.tier-strong{background:#dcfce7;color:#16a34a;padding:2px 8px;border-radius:10px;font-size:11px;border:1px solid #bbf7d0}
.tier-maybe{background:#fff7ed;color:#ea580c;padding:2px 8px;border-radius:10px;font-size:11px;border:1px solid #fed7aa}
.tier-low{background:#f1f5f9;color:#64748b;padding:2px 8px;border-radius:10px;font-size:11px;border:1px solid #e2e8f0}
.btn{display:inline-block;padding:7px 16px;border-radius:5px;text-decoration:none;font-size:13px;
     cursor:pointer;border:1px solid transparent;background:#2563eb;color:#fff}
.btn:hover{background:#1d4ed8}
.btn-sm{padding:4px 10px;font-size:12px}
.btn-outline{background:#fff;color:#2563eb;border-color:#2563eb}
.btn-outline:hover{background:#eff6ff}
.filter-bar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;align-items:center}
.filter-bar a{padding:5px 13px;border:1px solid #e2e8f0;border-radius:16px;font-size:13px;
              text-decoration:none;color:#475569;background:#fff}
.filter-bar a.active,.filter-bar a:hover{background:#2563eb;color:#fff;border-color:#2563eb}
.pager{display:flex;gap:6px;margin-top:16px;justify-content:center}
.pager a,.pager span{padding:6px 12px;border:1px solid #e2e8f0;border-radius:4px;
                     font-size:13px;text-decoration:none;color:#475569;background:#fff}
.pager a:hover{background:#eff6ff}
.pager .cur{background:#2563eb;color:#fff;border-color:#2563eb}
h2{font-size:18px;margin:0 0 14px;color:#1a1a2e}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden}
th{background:#f1f5f9;padding:10px 12px;text-align:left;font-size:13px;color:#475569;
   border-bottom:1px solid #e2e8f0}
td{padding:10px 12px;font-size:13px;border-bottom:1px solid #f1f5f9;vertical-align:top}
tr:last-child td{border-bottom:none}
/* Form */
.form-section{margin-bottom:22px}
label.field-label{display:block;font-size:14px;font-weight:600;color:#374151;margin-bottom:6px}
input[type=range]{width:100%;accent-color:#2563eb;cursor:pointer}
.score-label{font-size:16px;font-weight:bold;color:#1a1a2e;margin:6px 0 4px;min-height:26px}
.score-hint{font-size:12px;color:#64748b;margin-bottom:12px}
/* Tag pill toggles */
.tag-group-label{font-size:12px;color:#6b7280;font-weight:600;margin:12px 0 6px}
.tag-pills{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:4px}
.tag-pill{padding:4px 12px;border:1px solid #d1d5db;border-radius:9999px;font-size:13px;
          cursor:pointer;background:#f9fafb;color:#374151;user-select:none;transition:all .1s}
.tag-pill.selected{background:#2563eb;border-color:#2563eb;color:#fff}
textarea.field{width:100%;padding:10px;border:1px solid #d1d5db;border-radius:6px;
               font-size:14px;resize:vertical;min-height:80px}
.detail-back{font-size:13px;color:#2563eb;text-decoration:none;display:inline-block;margin-bottom:12px}
.detail-back:hover{text-decoration:underline}
.existing-fb{background:#f0fdf4;border:1px solid #bbf7d0;border-radius:6px;
             padding:10px 14px;font-size:13px;margin-bottom:18px;color:#166534}
</style>"""

_NAV = """<nav>
  <span class="brand">🎯 Job Scraper</span>
  <a href="/jobs">All Jobs</a>
  <a href="/jobs?tier=strong_match">Strong</a>
  <a href="/jobs?tier=maybe">Maybe</a>
  <a href="/feedback">Feedback</a>
</nav>"""


def _page_html(title: str, body: str) -> str:
    return f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8"><title>{title} — Job Scraper</title>
{_CSS}
</head><body>{_NAV}<div class="wrap">{body}</div></body></html>"""


# ── Job list ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return redirect(url_for("job_list"))


@app.route("/jobs")
def job_list():
    conn   = _get_db()
    tier   = request.args.get("tier", "all")
    page   = max(1, int(request.args.get("page", 1)))
    offset = (page - 1) * PAGE_SIZE

    # Filter by raw score, not tier label — keeps UI in sync after any
    # scoring-prompt changes that might relabel existing rows.
    # Boundaries mirror extractor_scorer.py + settings.yaml: strong >= 8, maybe 5–7, low <= 4.
    where = ""
    params: list = []
    if tier == "strong_match":
        where  = "WHERE relevance_score >= 8"
    elif tier == "maybe":
        where  = "WHERE relevance_score >= 5 AND relevance_score < 8"
    elif tier == "not_relevant":
        where  = "WHERE relevance_score <= 4"

    total = conn.execute(
        f"SELECT COUNT(*) FROM jobs {where}", params
    ).fetchone()[0]

    rows = conn.execute(
        f"""SELECT id, title, organization, location, source, relevance_score,
                   relevance_tier, deadline, description_snippet, tags
            FROM jobs {where}
            ORDER BY relevance_score DESC,
                     CASE WHEN deadline IS NULL THEN 1 ELSE 0 END,
                     deadline ASC
            LIMIT ? OFFSET ?""",
        params + [PAGE_SIZE, offset],
    ).fetchall()

    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    # Filter bar
    filters = [
        ("all",          "All", tier == "all"),
        ("strong_match", "Strong ≥8", tier == "strong_match"),
        ("maybe",        "Maybe 5–7", tier == "maybe"),
        ("not_relevant", "Low ≤4",    tier == "not_relevant"),
    ]
    filter_html = '<div class="filter-bar">' + "".join(
        f'<a href="/jobs?tier={k}" class="{"active" if active else ""}">{label}</a>'
        for k, label, active in filters
    ) + f"<span style='margin-left:auto;font-size:13px;color:#94a3b8'>{total} jobs</span></div>"

    # Job cards
    cards = []
    for r in rows:
        tier_cls = {"strong_match": "tier-strong", "maybe": "tier-maybe"}.get(
            r["relevance_tier"] or "", "tier-low"
        )
        score   = r["relevance_score"] or 0
        bar     = "●" * score + "○" * (10 - score)
        tags    = json.loads(r["tags"] or "[]")
        tag_html = " ".join(f'<span class="tag">{t}</span>' for t in tags)
        dl_badge = _deadline_badge(r["deadline"])

        cards.append(f"""<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:6px">
    <span class="card-title">{r['title'] or '—'}</span>
    <span class="{tier_cls}">{score}/10</span>
  </div>
  <div class="card-meta">
    🏢 {r['organization'] or '—'} &nbsp;|&nbsp;
    📍 {r['location'] or '—'} &nbsp;|&nbsp;
    <span style="color:#94a3b8;font-size:11px">via {r['source']}</span>
    {f"&nbsp;|&nbsp; {dl_badge}" if dl_badge else ""}
  </div>
  <div class="snippet">{r['description_snippet'] or ''}</div>
  {f'<div style="margin:4px 0">{tag_html}</div>' if tags else ''}
  <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">
    <a class="btn btn-sm btn-outline" href="/jobs/{r['id']}">Details + Feedback</a>
  </div>
</div>""")

    cards_html = "\n".join(cards) if cards else "<p style='color:#94a3b8'>No jobs found.</p>"

    # Feedback-saved banner (set after form submit via ?feedback=saved)
    banner_html = ""
    if request.args.get("feedback") == "saved":
        banner_html = (
            '<div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:6px;'
            'padding:10px 14px;font-size:13px;color:#166534;margin-bottom:12px">'
            '✅ Feedback saved — Claude will use this in future scoring runs.'
            '</div>'
        )

    # Calibration health footer
    calib_html = ""
    try:
        from feedback.store import get_feedback_summary
        fb_summary = get_feedback_summary()
        n_total   = fb_summary["total"]
        n_liked   = len(fb_summary["liked"]) + len(fb_summary.get("applied", []))
        n_passed  = len(fb_summary["passed"])
        if n_total == 0:
            calib_html = (
                '<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;'
                'padding:10px 14px;font-size:13px;color:#64748b;margin-bottom:12px">'
                '📊 No feedback yet — rate jobs to calibrate future scoring.'
                '</div>'
            )
        else:
            calib_html = (
                '<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;'
                'padding:10px 14px;font-size:13px;color:#64748b;margin-bottom:12px">'
                f'📊 Claude has learned from <strong>{n_total} rating{"s" if n_total != 1 else ""}</strong> '
                f'({n_liked} interested · {n_passed} passed). '
                'Keep rating to improve accuracy.'
                '</div>'
            )
    except Exception:
        pass

    # Pager
    def plink(p):
        return f"/jobs?tier={tier}&page={p}"

    pager_items = []
    if page > 1:
        pager_items.append(f'<a href="{plink(page-1)}">← Prev</a>')
    for p in range(max(1, page - 2), min(total_pages, page + 2) + 1):
        cls = 'class="cur"' if p == page else ""
        pager_items.append(f'<a href="{plink(p)}" {cls}>{p}</a>')
    if page < total_pages:
        pager_items.append(f'<a href="{plink(page+1)}">Next →</a>')
    pager_html = f'<div class="pager">{"".join(pager_items)}</div>' if total_pages > 1 else ""

    body = f"""<h2>Jobs</h2>
{banner_html}{calib_html}{filter_html}
{cards_html}
{pager_html}"""
    return _page_html("Jobs", body)


# ── Job detail + feedback form ─────────────────────────────────────────────────

@app.route("/jobs/<int:job_id>")
def job_detail(job_id: int):
    conn = _get_db()
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        return _page_html("Not found", "<p>Job not found.</p>"), 404
    _log_view(job_id)

    # Any existing feedback for this job?
    fb = conn.execute(
        "SELECT * FROM feedback WHERE job_id = ? ORDER BY timestamp DESC LIMIT 1",
        (str(job_id),),
    ).fetchone()

    tags     = json.loads(job["tags"] or "[]")
    tag_html = " ".join(f'<span class="tag">{t}</span>' for t in tags)
    score    = job["relevance_score"] or 0
    bar      = "●" * score + "○" * (10 - score)
    dl_badge = _deadline_badge(job["deadline"])

    existing_fb_html = ""
    if fb:
        fb_tags = json.loads(fb["tags"] or "[]") if fb["tags"] else []
        fb_score = fb["relevance_score"] or "—"
        applied_btn = (
            f'<form method="post" action="/jobs/{job_id}/feedback" style="display:inline">'
            '<input type="hidden" name="relevance_score" value="10">'
            '<input type="hidden" name="action_override" value="applied">'
            '<button type="submit" style="background:#16a34a;color:#fff;padding:6px 14px;'
            'border-radius:5px;border:none;cursor:pointer;font-size:13px;margin-top:10px">'
            '✅ Applied</button></form>'
        )
        existing_fb_html = f"""<div class="existing-fb">
  ✅ Feedback already submitted — Score: <strong>{fb_score}/10</strong>
  {f"&nbsp;|&nbsp; Tags: {', '.join(fb_tags)}" if fb_tags else ""}
  {f"<br>Comment: {fb['comment']}" if fb['comment'] else ""}
  <br><small style="color:#16a34a">You can submit again to update it.</small>
  <br>{applied_btn} &nbsp; <small style="color:#16a34a">Mark as actually applied</small>
</div>"""

    # Build pill-toggle tag section
    def _pill_group(label: str, tag_list: list) -> str:
        pills = "".join(
            f'<span class="tag-pill" data-tag="{t}" onclick="toggleTag(this)">{t}</span>'
            for t in tag_list
        )
        return f'<div class="tag-group-label">{label}</div><div class="tag-pills">{pills}</div>'

    body = f"""<a class="detail-back" href="/jobs">← Back to all jobs</a>

<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px">
    <h2 style="margin:0">{job['title'] or '—'}</h2>
    <a href="{job['url']}" target="_blank" class="btn">→ Apply / View</a>
  </div>

  <div class="card-meta" style="margin-top:8px">
    🏢 {job['organization'] or '—'} &nbsp;|&nbsp;
    📍 {job['location'] or '—'} &nbsp;|&nbsp;
    📋 {job['contract_type'] or '—'} &nbsp;|&nbsp;
    <span style="color:#94a3b8;font-size:11px">via {job['source']}</span>
    {f"<br style='margin:4px 0'>{dl_badge}" if dl_badge else ""}
  </div>

  <div class="snippet" style="margin:10px 0">{job['description_snippet'] or ''}</div>

  <div class="reason" style="margin:6px 0">
    <strong>Score:</strong> {score}/10 &nbsp;
    <span class="score-bar">{bar}</span>
  </div>
  <div class="reason"><em>{job['relevance_reason'] or ''}</em></div>

  {f'<div style="margin-top:8px">{tag_html}</div>' if tags else ''}
</div>

{existing_fb_html}

<div class="card">
  <h2 style="margin-top:0">Your Feedback</h2>
  <form method="post" action="/jobs/{job_id}/feedback" id="fb-form">

    <div class="form-section">
      <label class="field-label">How relevant is this job for you?</label>
      <input type="range" id="slider" name="relevance_score"
             min="1" max="10" value="5"
             oninput="onSlide(this.value)">
      <div class="score-label" id="score-label">— move slider to rate</div>
      <div class="score-hint">1–3 = not relevant &nbsp;·&nbsp; 4–6 = possibly relevant &nbsp;·&nbsp; 7–10 = strong match</div>
    </div>

    <div class="form-section">
      <label class="field-label">Why? <span style="font-weight:normal;color:#94a3b8">(optional tags — pick any)</span></label>
      <div id="tag-section">
        {_pill_group("Why pass?", PASS_TAGS)}
        {_pill_group("Why like?", LIKE_TAGS)}
      </div>
      <input type="hidden" name="tags" id="tags-hidden" value="">
    </div>

    <div class="form-section">
      <label class="field-label" for="comment">Anything else?</label>
      <textarea class="field" id="comment" name="comment"
        placeholder="Anything else? You can also paste a job you loved as an example."></textarea>
    </div>

    <button type="submit" class="btn">Submit feedback</button>
  </form>
</div>

<script>
var sliderTouched = false;
var selectedTags = [];

function onSlide(val) {{
  sliderTouched = true;
  val = parseInt(val);
  var labels = ['','Not relevant','Not relevant','Not relevant',
                'Possibly relevant','Possibly relevant','Possibly relevant',
                'Strong match','Strong match','Strong match','Strong match'];
  document.getElementById('score-label').textContent = val + '/10 — ' + labels[val];
}}

function toggleTag(el) {{
  var tag = el.getAttribute('data-tag');
  if (el.classList.contains('selected')) {{
    el.classList.remove('selected');
    selectedTags = selectedTags.filter(function(t) {{ return t !== tag; }});
  }} else {{
    el.classList.add('selected');
    selectedTags.push(tag);
  }}
  document.getElementById('tags-hidden').value = selectedTags.join('||');
}}

document.getElementById('fb-form').addEventListener('submit', function(e) {{
  if (!sliderTouched) {{
    e.preventDefault();
    alert('Please move the slider to rate this job before submitting.');
    return;
  }}
  // Convert tags hidden field to individual fields for getlist()
  var tagsVal = document.getElementById('tags-hidden').value;
  document.getElementById('tags-hidden').name = '';
  if (tagsVal) {{
    tagsVal.split('||').forEach(function(tag) {{
      var inp = document.createElement('input');
      inp.type = 'hidden';
      inp.name = 'tags';
      inp.value = tag;
      document.getElementById('fb-form').appendChild(inp);
    }});
  }}
}});
</script>"""

    return _page_html(job["title"] or "Job detail", body)


# ── Feedback submit ─────────────────────────────────────────────────────────────

@app.route("/jobs/<int:job_id>/feedback", methods=["POST"])
def submit_feedback(job_id: int):
    conn = _get_db()
    job = conn.execute(
        "SELECT id, url, title, organization, relevance_score FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    if job is None:
        return _page_html("Not found", "<p>Job not found.</p>"), 404

    try:
        relevance_score = int(request.form.get("relevance_score", 5))
        relevance_score = max(1, min(10, relevance_score))
    except (ValueError, TypeError):
        relevance_score = 5

    raw_tags = request.form.getlist("tags")
    tags = [t for t in raw_tags if t in ALLOWED_TAGS]
    comment = request.form.get("comment", "").strip()

    # Detect "Applied" action — form value from the Applied button
    action_override = request.form.get("action_override", "")
    if action_override == "applied":
        action = "applied"
    else:
        action = "like" if relevance_score >= 7 else "pass"

    # Write to SQLite (authoritative)
    _write_feedback_sqlite(str(job_id), relevance_score, tags, comment)

    # Write to JSON store (for profile_updater prompt additions)
    _write_feedback_json(
        str(job_id), job["url"] or "", job["title"] or "",
        job["organization"] or "", relevance_score, action, comment, tags=tags,
    )

    # Update org boost in profile.yaml (requires 2+ strong signals per org)
    try:
        from feedback.profile_updater import update_liked_organizations
        update_liked_organizations()
    except Exception:
        log.warning("update_liked_organizations failed — continuing")

    log.info(f"Feedback submitted: job_id={job_id} score={relevance_score} action={action} tags={tags}")

    return redirect(url_for("job_list", feedback="saved"))


# ── Feedback list ───────────────────────────────────────────────────────────────

@app.route("/feedback")
def feedback_list():
    conn  = _get_db()
    page  = max(1, int(request.args.get("page", 1)))
    offset = (page - 1) * PAGE_SIZE

    total = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
    rows  = conn.execute(
        """SELECT f.id, f.job_id, f.relevance_score, f.tags, f.comment,
                  f.timestamp, j.title, j.organization
           FROM feedback f
           LEFT JOIN jobs j ON j.id = CAST(f.job_id AS INTEGER)
           ORDER BY f.timestamp DESC
           LIMIT ? OFFSET ?""",
        (PAGE_SIZE, offset),
    ).fetchall()

    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    if rows:
        trows = []
        for r in rows:
            row_tags = json.loads(r["tags"] or "[]") if r["tags"] else []
            score = r["relevance_score"] or "—"
            score_style = ""
            if isinstance(score, int):
                if score >= 7: score_style = "color:#16a34a;font-weight:bold"
                elif score >= 4: score_style = "color:#d97706;font-weight:bold"
                else: score_style = "color:#dc2626;font-weight:bold"
            ts = (r["timestamp"] or "")[:16].replace("T", " ")
            trows.append(f"""<tr>
  <td><a href="/jobs/{r['job_id']}" style="color:#2563eb;text-decoration:none">{r['title'] or '—'}</a></td>
  <td>{r['organization'] or '—'}</td>
  <td style="{score_style}">{score}</td>
  <td>{", ".join(row_tags) or "—"}</td>
  <td style="max-width:220px;overflow:hidden;text-overflow:ellipsis">{r['comment'] or '—'}</td>
  <td style="white-space:nowrap;color:#94a3b8">{ts}</td>
</tr>""")

        table_html = f"""<table>
<thead><tr>
  <th>Job</th><th>Organisation</th><th>Score</th>
  <th>Tags</th><th>Comment</th><th>Date</th>
</tr></thead>
<tbody>{"".join(trows)}</tbody>
</table>"""
    else:
        table_html = "<p style='color:#94a3b8'>No feedback yet. Open a job and rate it.</p>"

    # Pager
    def plink(p):
        return f"/feedback?page={p}"
    pager_items = []
    if page > 1:
        pager_items.append(f'<a href="{plink(page-1)}">← Prev</a>')
    for p in range(max(1, page-2), min(total_pages, page+2)+1):
        cls = 'class="cur"' if p == page else ""
        pager_items.append(f'<a href="{plink(p)}" {cls}>{p}</a>')
    if page < total_pages:
        pager_items.append(f'<a href="{plink(page+1)}">Next →</a>')
    pager_html = f'<div class="pager">{"".join(pager_items)}</div>' if total_pages > 1 else ""

    body = f"""<h2>Feedback History &nbsp;<span style="font-size:14px;font-weight:normal;color:#94a3b8">({total} entries)</span></h2>
{table_html}
{pager_html}"""
    return _page_html("Feedback", body)


# ── Email button compatibility (legacy /fb route) ──────────────────────────────

@app.route("/fb")
def fb_compat():
    """Redirect email 'Interested / Pass' buttons to the rich job-detail page."""
    job_id = request.args.get("id", "")
    if job_id:
        return redirect(url_for("job_detail", job_id=int(job_id)))
    return redirect(url_for("job_list"))


@app.route("/comment_form")
def comment_form_compat():
    """Legacy comment form — redirects to job detail."""
    job_id = request.args.get("id", "")
    if job_id:
        return redirect(url_for("job_detail", job_id=int(job_id)))
    return redirect(url_for("job_list"))


@app.route("/comment", methods=["POST"])
def comment_compat():
    """Legacy comment POST — save to JSON store and redirect."""
    job_id  = request.form.get("job_id", "")
    url     = request.form.get("url", "")
    title   = request.form.get("title", "")
    org     = request.form.get("organization", "")
    score   = int(request.form.get("score", 5))
    action  = request.form.get("action", "like")
    comment = request.form.get("comment", "").strip()
    _write_feedback_json(job_id, url, title, org, score, action, comment)
    if job_id:
        return redirect(url_for("job_detail", job_id=int(job_id)))
    return redirect(url_for("job_list"))


@app.route("/skip-suggestion")
def skip_suggestion():
    """Desktop fallback for skipping a source suggestion.
    Mobile email links use the CF Worker route instead."""
    from datetime import datetime
    suggestion_id = request.args.get("id", type=int)
    if not suggestion_id:
        return "Missing id", 400
    db = _get_db()
    result = db.execute(
        "UPDATE source_suggestions SET status='skipped', skipped_at=? WHERE id=?",
        (datetime.utcnow().isoformat(), suggestion_id),
    )
    db.commit()
    if result.rowcount == 0:
        return "Suggestion not found", 404
    return "Skipped. You won't see this suggestion again.", 200


@app.route("/health")
def health():
    return "OK", 200


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # ── Log to file (always) + console (when available) ────────────────────────
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "server.log"

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=2 * 1024 * 1024, backupCount=2, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    ))
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    # Only add console handler when stdout is available (not pythonw.exe)
    if sys.stdout and sys.stdout.fileno() >= 0:
        try:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
            root_logger.addHandler(console_handler)
        except Exception:
            pass

    # ── Port collision guard ───────────────────────────────────────────────────
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", 5001)) == 0:
            log.info("Port 5001 already in use — another server instance is running. Exiting.")
            sys.exit(0)

    pid_path = Path(__file__).parent / ".server.pid"
    pid_path.write_text(str(os.getpid()))
    log.info("Feedback server starting at http://localhost:5001")
    try:
        app.run(host="127.0.0.1", port=5001, debug=False, use_reloader=False)
    finally:
        pid_path.unlink(missing_ok=True)
