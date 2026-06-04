"""
Reads stored feedback history and generates:
  1. Extra system-prompt text (few-shot calibration examples for Claude)
  2. HTML footer block for the email digest
  3. Liked-org boost in config/profile.yaml (via update_liked_organizations)
  4. Decay-weighted per-criterion preference averages (via update_explicit_preferences)
All write functions degrade gracefully if the feedback store is missing or empty.
"""
from __future__ import annotations
import logging
import math
from collections import Counter
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

_PROFILE_PATH = Path(__file__).parent.parent / "config" / "profile.yaml"


def _item_note(item: dict) -> str:
    """Build the annotation suffix for a few-shot example line."""
    parts = []
    criteria = item.get("criteria")
    if criteria:
        formatted = ", ".join(f"{k}:{v}" for k, v in criteria.items())
        parts.append(f"[criteria: {formatted}]")
    elif item.get("tags"):
        parts.append(f"[{', '.join(item['tags'])}]")   # backward compat for old items
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

    return "\n".join(lines)


_HALF_LIFE_DAYS = 90  # preference feedback older than 90 days has half the weight


def _write_profile_atomic(profile: dict) -> None:
    """Write profile dict to profile.yaml via tmp+rename (atomic on Windows NTFS)."""
    import yaml
    tmp = _PROFILE_PATH.with_suffix(".tmp")
    tmp.write_text(
        yaml.dump(profile, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    tmp.replace(_PROFILE_PATH)


def _decay_weight(feedback_date_str: str, today: date) -> float:
    """Exponential decay weight for a feedback item. Returns 1.0 at age=0, ~0.5 at age=90 days."""
    age_days = (today - date.fromisoformat(feedback_date_str[:10])).days
    return math.exp(-math.log(2) * max(age_days, 0) / _HALF_LIFE_DAYS)


def _weighted_mean(scores_with_dates: list[tuple[float, str]], today: date) -> float | None:
    """Decay-weighted mean. weighted_mean = sum(w_i * score_i) / sum(w_i). Returns None if empty."""
    if not scores_with_dates:
        return None
    weights = [_decay_weight(d, today) for _, d in scores_with_dates]
    return sum(w * s for w, (s, _) in zip(weights, scores_with_dates)) / sum(weights)


def update_explicit_preferences() -> None:
    """
    Reads criteria scores from feedback_store.json, computes decay-weighted averages
    per dimension, and writes them to profile.yaml:explicit_preferences.
    Called after each feedback submission alongside update_liked_organizations().
    Items without a 'criteria' key are skipped gracefully.
    """
    try:
        from feedback.store import get_all
        import yaml

        items = get_all()
        today = date.today()
        dimensions = ["topic_fit", "methods_fit", "org_appeal", "career_fit", "location_fit"]

        new_weights: dict = {}
        for dim in dimensions:
            scores_with_dates = [
                (float(item["criteria"][dim]), item["timestamp"])
                for item in items
                if item.get("criteria") and dim in item["criteria"]
            ]
            wm = _weighted_mean(scores_with_dates, today)
            if wm is not None:
                new_weights[dim] = {
                    "weighted_mean": round(wm, 2),
                    "n_samples": len(scores_with_dates),
                    "last_updated": today.isoformat(),
                }

        if not new_weights:
            return

        profile = yaml.safe_load(_PROFILE_PATH.read_text(encoding="utf-8")) or {}
        profile["explicit_preferences"] = new_weights
        _write_profile_atomic(profile)
        log.info(f"[profile_updater] explicit_preferences updated: {list(new_weights.keys())}")

    except Exception:
        log.exception("[profile_updater] update_explicit_preferences failed")


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
        _write_profile_atomic(profile)
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
