"""
Reads stored feedback history and generates:
  1. Extra system-prompt text (few-shot calibration examples for Claude)
  2. HTML footer block for the email digest
Both functions degrade gracefully if the feedback store is missing or empty.
"""
from __future__ import annotations
import logging

log = logging.getLogger(__name__)


def generate_prompt_additions() -> str:
    """
    Returns a block of text to append to the scoring system prompt.
    Uses the most recent liked/passed jobs as calibration examples.
    """
    try:
        from feedback.store import get_feedback_summary
        summary = get_feedback_summary()
    except Exception:
        log.debug("feedback.store not available — skipping prompt additions")
        return ""

    if summary["total"] == 0:
        return ""

    lines = [
        "\n\n══════════════════════════════════════════════════════════",
        "USER FEEDBACK (calibrate scores based on these past reactions):",
        "══════════════════════════════════════════════════════════",
    ]

    liked  = summary["liked"][-10:]
    passed = summary["passed"][-10:]

    if liked:
        lines.append("\nJOBS USER LIKED — scored too low; boost similar roles:")
        for item in liked:
            note = f'  ← "{item["comment"]}"' if item.get("comment") else ""
            lines.append(f'  ✅ [{item["score_given"]}/10] {item["title"]} @ {item["organization"]}{note}')

    if passed:
        lines.append("\nJOBS USER PASSED ON — scored too high; reduce similar roles:")
        for item in passed:
            note = f'  ← "{item["comment"]}"' if item.get("comment") else ""
            lines.append(f'  ❌ [{item["score_given"]}/10] {item["title"]} @ {item["organization"]}{note}')

    return "\n".join(lines)


def build_feedback_footer_html() -> str:
    """Returns the HTML 'feedback status' block shown at the bottom of every digest."""
    try:
        from feedback.store import get_feedback_summary
        summary = get_feedback_summary()
    except Exception:
        return ""

    n_liked  = len(summary["liked"])
    n_passed = len(summary["passed"])
    total    = summary["total"]

    if total == 0:
        body = (
            "No feedback yet — use the <strong>✅ Interested</strong> / <strong>❌ Pass</strong> "
            "buttons above to calibrate future digests."
        )
    else:
        recent_pass_titles = [
            (x["title"][:38] + "…") if len(x["title"]) > 38 else x["title"]
            for x in summary["passed"][-3:]
        ]
        recent_like_titles = [
            (x["title"][:38] + "…") if len(x["title"]) > 38 else x["title"]
            for x in summary["liked"][-3:]
        ]
        parts = [f"<strong>{n_liked} interested · {n_passed} passed</strong> so far."]
        if recent_pass_titles:
            parts.append(f"Recent passes: {', '.join(recent_pass_titles)}")
        if recent_like_titles:
            parts.append(f"Recent likes: {', '.join(recent_like_titles)}")
        body = "<br>".join(parts)

    return f"""
<div style="margin-top:28px;padding:14px 16px;background:#f8fafc;border-radius:8px;
     border:1px solid #e2e8f0;font-size:13px;color:#475569;line-height:1.7">
  <strong>📊 Your feedback shapes this digest</strong><br>
  {body}<br>
  <span style="color:#94a3b8;font-size:12px">
    Buttons open <code>localhost:5001</code> — the server starts automatically at login
    (Windows scheduled task: <em>JobScraperFeedbackServer</em>).
  </span>
</div>"""
