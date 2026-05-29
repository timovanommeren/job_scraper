"""
Reads stored feedback history and generates:
  1. Extra system-prompt text (few-shot calibration examples for Claude)
  2. HTML footer block for the email digest
  3. Liked-org boost in config/profile.yaml (via update_liked_organizations)
Both functions degrade gracefully if the feedback store is missing or empty.
"""
from __future__ import annotations
import logging
from collections import Counter
from pathlib import Path

log = logging.getLogger(__name__)

_PROFILE_PATH = Path(__file__).parent.parent / "config" / "profile.yaml"


def _item_note(item: dict) -> str:
    """Build the annotation suffix for a few-shot example line."""
    parts = []
    if item.get("tags"):
        parts.append(f"[{', '.join(item['tags'])}]")
    if item.get("comment"):
        parts.append(f'"{item["comment"]}"')
    return ("  ← " + " ".join(parts)) if parts else ""


def generate_prompt_additions() -> str:
    """
    Returns a block of text to append to the scoring system prompt.
    Uses the most recent liked/passed/applied jobs as calibration examples.
    Pure read-only — never writes to disk.
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

    liked   = (summary["applied"] + summary["liked"])[-10:]  # applied = strongest signal
    passed  = summary["passed"][-10:]

    if liked:
        lines.append("\nJOBS USER LIKED/APPLIED TO — scored too low; boost similar roles:")
        for item in liked:
            action_label = "APPLIED" if item.get("action") == "applied" else str(item["score_given"]) + "/10"
            lines.append(f'  ✅ [{action_label}] {item["title"]} @ {item["organization"]}{_item_note(item)}')

    if passed:
        lines.append("\nJOBS USER PASSED ON — scored too high; reduce similar roles:")
        for item in passed:
            lines.append(f'  ❌ [{item["score_given"]}/10] {item["title"]} @ {item["organization"]}{_item_note(item)}')

    # Inject liked-org boost if profile.yaml has liked_organizations
    try:
        import yaml
        profile = yaml.safe_load(_PROFILE_PATH.read_text(encoding="utf-8")) or {}
        orgs = profile.get("liked_organizations", [])
        if orgs:
            lines.append(
                f"\nORGANISATIONS WITH STRONG TRACK RECORD (boost by +1–2 points by default): "
                + ", ".join(orgs)
            )
    except Exception:
        pass

    return "\n".join(lines)


def update_liked_organizations() -> None:
    """
    Reads feedback_store.json and writes liked_organizations to config/profile.yaml.
    Called after each feedback submission (desktop or phone) — NOT from generate_prompt_additions.
    Threshold: org must appear ≥2 times with score≥8 OR action='applied' before being added.
    Capped at 20 entries. Deduplicates. Never overwrites other profile.yaml fields.
    """
    try:
        from feedback.store import get_all
        import yaml

        items = get_all()

        # Count strong-signal mentions per org (score≥8 or applied)
        org_counts: Counter = Counter(
            item["organization"]
            for item in items
            if (
                item.get("organization")
                and (
                    item.get("action") == "applied"
                    or int(item.get("score_given", 0)) >= 8
                )
            )
        )

        # Only orgs with 2+ strong signals
        qualified = [org for org, count in org_counts.most_common() if count >= 2][:20]

        if not qualified:
            return

        # Load, update, save — preserving all other profile.yaml content
        profile = yaml.safe_load(_PROFILE_PATH.read_text(encoding="utf-8")) or {}
        existing = profile.get("liked_organizations", [])
        merged = list(dict.fromkeys(existing + qualified))[:20]  # deduplicate, cap

        if merged == existing:
            return  # nothing changed

        profile["liked_organizations"] = merged
        _PROFILE_PATH.write_text(
            yaml.dump(profile, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        log.info(f"[profile_updater] liked_organizations updated: {merged}")

    except Exception:
        log.exception("[profile_updater] update_liked_organizations failed")


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

    n_applied = len(summary.get("applied", []))
    if total == 0:
        body = (
            "No feedback yet — tap a number in the rating row above to calibrate future digests."
        )
    else:
        recent_pass_titles = [
            (x["title"][:38] + "…") if len(x["title"]) > 38 else x["title"]
            for x in summary["passed"][-3:]
        ]
        recent_like_titles = [
            (x["title"][:38] + "…") if len(x["title"]) > 38 else x["title"]
            for x in (summary.get("applied", []) + summary["liked"])[-3:]
        ]
        applied_str = f" · {n_applied} applied" if n_applied else ""
        parts = [f"<strong>{n_liked} interested · {n_passed} passed{applied_str}</strong> so far."]
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
