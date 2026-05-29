"""
Weekly field-intelligence recommender.

Analyses high-rated jobs (score >= 8) from the last 90 days to build a field
profile summary and suggest up to 3 organisations worth adding as scrapers.
Suggestions are stored in source_suggestions and surfaced in the weekly digest.
"""

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

import requests
import yaml
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

# ── Display name mapping ───────────────────────────────────────────────────────
# Maps scraper source_name slugs to human-readable names used in the Claude
# prompt so the model knows which organisations are already covered.

SOURCE_NAME_TO_DISPLAY: dict[str, str] = {
    "euraxess": "Euraxess",
    "academictransfer": "AcademicTransfer",
    "rand": "RAND Corporation",
    "case_poland": "CASE Poland",
    "jrc": "JRC (Joint Research Centre)",
    "impactpool": "Impactpool",
    "busara": "Busara Center for Behavioral Economics",
    "wodc": "WODC",
    "scp": "SCP (Sociaal en Cultureel Planbureau)",
    "trimbos": "Trimbos-instituut",
    "fgv": "FGV (Fundação Getulio Vargas)",
    "epso_bluebook": "EPSO Blue Book (European Commission traineeships)",
    "eucareers": "EU Careers (EU agency traineeships)",
    # Disabled scrapers — still covered in the sense the user knows about them.
    "uncareers": "UN Careers",
    "oecd": "OECD",
    "bit": "Behavioural Insights Team (BIT)",
    "tni": "Transnational Institute (TNI)",
}


# ── Pydantic models ────────────────────────────────────────────────────────────

class OrgSuggestion(BaseModel):
    name: str
    country: str
    description: str
    candidate_url: str
    validated_url: Optional[str] = None


class SourceRecommendation(BaseModel):
    profile_summary: str
    suggestions: list[OrgSuggestion]


# ── Data retrieval ─────────────────────────────────────────────────────────────

def get_high_rated_jobs(db, min_score: int = 8, lookback_days: int = 90) -> list[dict]:
    """
    Return jobs with score >= min_score (from LLM or user feedback) seen in the
    last lookback_days days.  User feedback score takes precedence over LLM score.
    Does NOT filter out orgs the user has skipped — skipped orgs are excluded
    from the suggestion output only, not from field-profile data collection.
    """
    rows = db.execute(
        """
        SELECT
            j.id,
            j.title,
            j.organization,
            j.location,
            j.source,
            j.relevance_score   AS llm_score,
            COALESCE(f.relevance_score, j.relevance_score) AS effective_score,
            j.tags,
            j.relevance_reason,
            j.first_seen_at
        FROM jobs j
        LEFT JOIN feedback f ON CAST(j.id AS TEXT) = f.job_id
        WHERE
            COALESCE(f.relevance_score, j.relevance_score) >= ?
            AND j.first_seen_at >= datetime('now', ? || ' days')
        ORDER BY effective_score DESC, j.first_seen_at DESC
        """,
        (min_score, f"-{lookback_days}"),
    ).fetchall()
    return [dict(r) for r in rows]


def _get_skipped_org_names(db) -> list[str]:
    """Return display names of orgs the user has skipped."""
    rows = db.execute(
        "SELECT DISTINCT org_name FROM source_suggestions WHERE status = 'skipped'"
    ).fetchall()
    return [r["org_name"] for r in rows]


# ── Claude analysis ────────────────────────────────────────────────────────────

def analyze_and_suggest(
    jobs: list[dict],
    excluded_display_names: list[str],
    already_covered: list[str],
    client,
) -> SourceRecommendation:
    """
    Call Claude via instructor to produce a field profile summary and up to 3
    new organisation suggestions.  Returns an empty suggestion list on failure.
    """
    model = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

    job_lines = []
    for j in jobs[:50]:  # cap context size
        tags = j.get("tags") or ""
        job_lines.append(
            f"- {j['title']} @ {j['organization'] or 'Unknown'} "
            f"[{j['source']}] score={j['effective_score']} tags={tags}"
        )
    jobs_block = "\n".join(job_lines)

    already_str = "\n".join(f"  - {n}" for n in sorted(already_covered))
    excluded_str = (
        "\n".join(f"  - {n}" for n in excluded_display_names)
        if excluded_display_names
        else "  (none)"
    )

    prompt = f"""You are a research-career intelligence assistant for Timo van Ommeren, who is
job-hunting in EU/UN institutions, think tanks, and social-science research institutes.

Below are the {len(jobs)} highest-rated job postings Timo has seen recently (score >= 8/10):

{jobs_block}

Already covered by existing scrapers (do NOT suggest these):
{already_str}

Also excluded (user has dismissed these before — do NOT suggest them):
{excluded_str}

Your task:
1. Write a 2-3 sentence "field profile summary" describing the pattern you see
   in these high-rated jobs (e.g. sectors, methodologies, geographies, contract types).
2. Suggest up to 3 organisations NOT in the covered or excluded lists that Timo
   would likely find compelling, based on this pattern.  For each suggestion:
   - name: official organisation name
   - country: country where headquartered
   - description: 1 sentence on what they do and why relevant to Timo
   - candidate_url: your best guess at the direct URL for their careers/jobs page

Return fewer than 3 suggestions if you are genuinely unsure — quality over quantity.
Do not suggest job aggregators or platforms; focus on specific organisations.
"""

    try:
        result = client.chat.completions.create(
            model=model,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
            response_model=SourceRecommendation,
        )
        return result
    except ValidationError:
        logger.warning("source_recommender: instructor ValidationError — returning empty suggestions")
        return SourceRecommendation(
            profile_summary="(Field profile unavailable this week — API validation error.)",
            suggestions=[],
        )
    except Exception:
        logger.exception("source_recommender: unexpected error during Claude call")
        return SourceRecommendation(
            profile_summary="(Field profile unavailable this week — API error.)",
            suggestions=[],
        )


# ── URL validation ─────────────────────────────────────────────────────────────

def validate_url(suggestion: OrgSuggestion) -> bool:
    """
    Try to confirm a reachable careers URL for the suggestion.
    Step 1: direct HTTP GET on candidate_url.
    Step 2: root-domain fallback (e.g. https://example.org).
    Step 3: DuckDuckGo HTML search for "<org name> careers jobs".
    Sets suggestion.validated_url and returns True if any step succeeds.
    """
    headers = {"User-Agent": "Mozilla/5.0 (compatible; job-scraper-bot/1.0)"}
    timeout = 10

    # Step 1 — direct candidate URL
    try:
        r = requests.get(suggestion.candidate_url, headers=headers, timeout=timeout,
                         allow_redirects=True)
        if r.status_code < 400:
            suggestion.validated_url = r.url  # final URL after redirects
            return True
    except Exception:
        pass

    # Step 2 — root domain fallback
    try:
        from urllib.parse import urlparse
        parsed = urlparse(suggestion.candidate_url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        if root != suggestion.candidate_url.rstrip("/"):
            r = requests.get(root, headers=headers, timeout=timeout, allow_redirects=True)
            if r.status_code < 400:
                suggestion.validated_url = r.url
                return True
    except Exception:
        pass

    # Step 3 — DuckDuckGo HTML search
    try:
        query = f"{suggestion.name} careers jobs"
        ddg_url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}"
        r = requests.get(ddg_url, headers=headers, timeout=timeout)
        if r.status_code == 200:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.select("a.result__url"):
                href = a.get("href", "")
                if href.startswith("http"):
                    suggestion.validated_url = href
                    return True
    except Exception:
        pass

    logger.info(f"source_recommender: could not validate URL for '{suggestion.name}' — dropping suggestion")
    return False


# ── Top-level entry point ──────────────────────────────────────────────────────

def _load_min_jobs() -> int:
    settings_path = os.path.join(os.path.dirname(__file__), "..", "config", "settings.yaml")
    try:
        with open(settings_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return int(data.get("source_recommender", {}).get("min_jobs", 5))
    except Exception:
        return 5


def generate_suggestions(db=None, client=None, test_mode: bool = False) -> Optional[SourceRecommendation]:
    """
    Main entry point called from notifier/gmail.py during weekly digest.

    db may be None (or omitted) — in that case a fresh connection is opened
    internally.  This is necessary when called from a ThreadPoolExecutor worker
    because SQLite connections cannot be shared across threads.

    - Queries high-rated jobs; returns None if below the minimum threshold.
    - Calls Claude to produce a field profile + up to 3 org suggestions.
    - Validates suggestion URLs in parallel (ThreadPoolExecutor, max 3 workers).
    - Saves validated suggestions to source_suggestions (skipped in test_mode).
    - Returns SourceRecommendation (possibly with empty suggestions list).

    Returns None if:
    - Not enough high-rated jobs to build a meaningful profile.
    - A fatal exception occurs (logged at WARNING, digest continues unaffected).
    """
    _own_db = False
    if db is None:
        from db.dedup import get_connection
        db = get_connection()
        _own_db = True
    try:
        # Drift guard: warn if registry diverges from SOURCE_NAME_TO_DISPLAY.
        # This is informational only — a missing mapping does not crash the digest.
        try:
            from main import build_scraper_registry
            registry = build_scraper_registry()
            live_names = set(registry.keys())
            mapped_names = set(SOURCE_NAME_TO_DISPLAY.keys())
            missing = live_names - mapped_names
            if missing:
                logger.warning(
                    f"source_recommender: SOURCE_NAME_TO_DISPLAY is missing entries for: {missing}. "
                    "Add them to keep coverage hints accurate."
                )
        except Exception:
            pass  # registry import failure is non-fatal

        min_jobs = _load_min_jobs()
        jobs = get_high_rated_jobs(db, min_score=8)

        if len(jobs) < min_jobs:
            logger.info(
                f"source_recommender: only {len(jobs)} high-rated job(s) found "
                f"(need {min_jobs}) — skipping suggestions this week"
            )
            return None

        if client is None:
            from agents.extractor_scorer import build_client
            client = build_client()

        already_covered = list(SOURCE_NAME_TO_DISPLAY.values())
        skipped_orgs = _get_skipped_org_names(db)

        recommendation = analyze_and_suggest(jobs, skipped_orgs, already_covered, client)

        if not recommendation.suggestions:
            return recommendation

        # Validate URLs in parallel
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(validate_url, s): s
                for s in recommendation.suggestions
            }
            validated = []
            for future in as_completed(futures):
                suggestion = futures[future]
                try:
                    ok = future.result()
                    if ok:
                        validated.append(suggestion)
                except Exception:
                    logger.warning(f"source_recommender: validation raised for '{suggestion.name}'")

        recommendation.suggestions = validated

        if not test_mode and recommendation.suggestions:
            now = datetime.now(timezone.utc).isoformat()
            for s in recommendation.suggestions:
                db.execute(
                    """
                    INSERT INTO source_suggestions
                        (suggested_at, org_name, org_country, org_description, careers_url, status)
                    VALUES (?, ?, ?, ?, ?, 'pending')
                    """,
                    (now, s.name, s.country, s.description, s.validated_url or s.candidate_url),
                )
            db.commit()
            logger.info(f"source_recommender: saved {len(recommendation.suggestions)} suggestion(s) to DB")

        return recommendation

    except Exception:
        logger.exception("source_recommender: generate_suggestions failed — weekly digest continues without suggestions")
        return None
    finally:
        if _own_db:
            db.close()
