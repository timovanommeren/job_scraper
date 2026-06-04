import os
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional
import yaml
import instructor
from anthropic import Anthropic, APIConnectionError, RateLimitError
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger(__name__)

# ── Pydantic output schema ─────────────────────────────────────────────────────

class JobPosting(BaseModel):
    title: str = Field(description="Job title as listed")
    organization: str = Field(description="Hiring organisation name")
    location: str = Field(description="Job location; 'Remote' if fully remote; 'Various' if multiple")
    contract_type: Optional[str] = Field(
        default=None,
        description="PhD position | postdoc | traineeship | full-time | part-time | consultancy | internship | other",
    )
    deadline: Optional[str] = Field(
        default=None,
        description=(
            "Application deadline as YYYY-MM-DD, or null if not found. "
            "Look for: 'apply by', 'closing date', 'deadline', 'applications close', "
            "'vacancy closes', 'uiterste sollicitatiedatum', 'sluitingsdatum'. "
            "Return null if ambiguous or not stated."
        ),
    )
    description_snippet: str = Field(
        description="First 250 characters of job description, HTML-stripped, in the original language",
    )
    tags: list = Field(
        description=(
            "2–5 keyword tags from this controlled vocabulary: "
            "statistics | R | Python | policy | EU-institution | UN-system | think-tank | "
            "drug-policy | public-health | data-science | methodology | PhD | traineeship | "
            "Dutch | behavioural-science | systematic-review | AI-research | development-finance"
        ),
    )
    relevance_score: int = Field(
        ge=1,
        le=10,
        description=(
            "1=completely irrelevant to Timo's profile, 10=perfect match. "
            "Score 8-10: apply immediately. Score 5-7: worth considering. Score 1-4: skip."
        ),
    )
    relevance_tier: str = Field(
        description="strong_match if score >= 8; maybe if score 5-7; not_relevant if score <= 4",
    )
    relevance_reason: str = Field(
        description="1-2 sentences. Cite specific role requirements vs Timo's actual skills/experience.",
    )


# ── System prompt ──────────────────────────────────────────────────────────────

_PROFILE_PATH = Path(__file__).resolve().parent.parent / "config" / "profile.yaml"

# Used only when profile.yaml is missing or unreadable — not a scoring substitute.
_FALLBACK_SYSTEM_PROMPT = (
    "You are a job relevance screener. config/profile.yaml could not be loaded. "
    "Score all jobs 5/10 until the profile is restored."
)


def _render_profile_prompt(profile: dict) -> str:
    """Assemble the LLM system prompt from the structured profile.yaml sections."""
    q = profile["qualifications"]
    t = profile["targets"]
    h = profile["hard_rules"]
    ep = profile.get("explicit_preferences", {})

    experience_lines = "\n".join(f"  * {e}" for e in q.get("experience", []))
    disqualify_lines = "\n".join(f"- {d}" for d in h.get("disqualify", []))
    penalise_lines   = "\n".join(f"- {p}" for p in h.get("penalise_hard", []))
    bonus_lines      = "\n".join(f"- {b}" for b in t.get("bonus_topics", []))
    liked_orgs       = profile.get("liked_organizations", [])

    prompt = f"""You are a job relevance screener for Timo van Ommeren.

TIMO'S PROFILE:
- Education: {q.get("education", "")}
- Thesis: {q.get("thesis", "")}
- Technical: {q.get("skills_technical", "")}
- Recent experience:
{experience_lines}
- Languages: {q.get("languages", "")}
- Location: {q.get("location", "")}
- Target sectors: {", ".join(t.get("sectors", []))}
- Target roles: {", ".join(t.get("roles", []))}
- Strong fit for: drug policy, public health, social/behavioural science, quantitative methods, evidence synthesis, AI/LLM in research

══════════════════════════════════════════════════════════
HARD DISQUALIFIERS — score MAX 2/10, do not score higher:
══════════════════════════════════════════════════════════
{disqualify_lines}

══════════════════════════════════════════════════════════
STRONG PENALTIES — reduce score by 2–3 points:
══════════════════════════════════════════════════════════
{penalise_lines}

══════════════════════════════════════════════════════════
IMPORTANT CLARIFICATION — PhD positions:
══════════════════════════════════════════════════════════
A "PhD position" or "PhD student position" or "doctoral researcher" typically means:
you are APPLYING TO BECOME a PhD student — you do NOT need a PhD to apply, only an MSc.
This is Timo's preferred career trajectory. Score these positively (6–9/10) if the topic
and institution match his profile. Do NOT confuse this with "Postdoc" which requires
a completed PhD.

══════════════════════════════════════════════════════════
SCORING SCALE:
══════════════════════════════════════════════════════════
- Score 8–10: Role directly matches Timo's background + target sector. Competitive application today.
- Score 6–7: Good fit but one element is off (slightly wrong sector, partial language match, etc.)
- Score 4–5: Interesting but notable mismatch (wrong country for permanent role, adjacent topic, wrong level)
- Score 2–3: Wrong level, wrong field, or fails a hard disqualifier
- Score 1: Completely irrelevant or impossible to apply for

══════════════════════════════════════════════════════════
BONUS POINTS (+1 to +2):
══════════════════════════════════════════════════════════
{bonus_lines}
"""

    if liked_orgs:
        orgs_str = ", ".join(liked_orgs)
        prompt += f"\nORGANISATIONS WITH STRONG TRACK RECORD (boost by +1–2 points by default): {orgs_str}\n"

    if ep and any(
        isinstance(v, dict) and v.get("n_samples", 0) >= 5 for v in ep.values()
    ):
        n_max = max(
            (v.get("n_samples", 0) for v in ep.values() if isinstance(v, dict)), default=0
        )
        lines = [
            f"- {dim}: {'positive' if data['weighted_mean'] >= 6 else 'negative'} signal"
            f" (avg {data['weighted_mean']:.1f}/10 across {data['n_samples']} rated jobs)"
            for dim, data in ep.items()
            if isinstance(data, dict) and data.get("n_samples", 0) >= 5
        ]
        if lines:
            prompt += f"\nLEARNED PREFERENCE SIGNALS (from {n_max} rated jobs):\n" + "\n".join(lines) + "\n"

    return prompt


def _get_full_system_prompt() -> str:
    """Assemble system prompt from structured profile.yaml + feedback calibration examples."""
    try:
        with open(_PROFILE_PATH, encoding="utf-8") as f:
            profile = yaml.safe_load(f) or {}
        prompt = _render_profile_prompt(profile)
    except Exception:
        logger.warning("Could not load config/profile.yaml — using fallback prompt")
        prompt = _FALLBACK_SYSTEM_PROMPT

    try:
        from feedback.profile_updater import generate_prompt_additions
        additions = generate_prompt_additions()
        if additions:
            prompt += additions
    except Exception:
        pass
    return prompt

# ── Pre-screen (Layer 2 cheap field-check) ─────────────────────────────────────

PRESCREEN_SYSTEM_PROMPT = (
    "You are a domain classifier. Answer only YES or NO, then one sentence of reasoning."
)

# Dispatch table: content_type -> classification question sent to the model.
# The system prompt (PRESCREEN_SYSTEM_PROMPT) is shared across all types.
# Add new entries here when funding_call / conference scrapers are built.
_PRESCREEN_PROMPTS: dict[str, str] = {
    "job": (
        "Is this job in the domain of quantitative social/behavioural science, "
        "public policy research methods, or public health?"
    ),
    "funding_call": (
        "Is this funding call relevant to quantitative social/behavioural science research, "
        "public policy methods, or public health? Answer True if it funds researchers "
        "in these domains."
    ),
    "conference": (
        "Is this conference or call for papers relevant to quantitative social/behavioural "
        "science, public policy research methods, or public health?"
    ),
}


class _PreScreenResult(BaseModel):
    relevant: bool = Field(
        description="True if the item matches the domain described in the question. False otherwise."
    )
    reason: str = Field(description="One sentence explaining the classification decision.")


def pre_screen(raw_job, client: instructor.Instructor, content_type: str = "job") -> tuple[bool, str]:
    """
    Cheap domain field-check before full LLM extraction.
    Returns (True, reason) if the item should proceed to scoring.
    Returns (False, reason) if it should be filtered.
    Fail-open: any exception returns (True, 'pre_screen_error').

    content_type dispatches to the appropriate question in _PRESCREEN_PROMPTS.
    Unknown types fall back to the 'job' prompt so new scrapers fail safe.
    """
    model = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
    max_chars = 300
    question = _PRESCREEN_PROMPTS.get(content_type, _PRESCREEN_PROMPTS["job"])
    user_content = (
        f"Title: {raw_job.title}\n"
        f"Text: {(raw_job.raw_text or '')[:max_chars]}\n\n"
        f"{question}"
    )
    try:
        result = client.chat.completions.create(
            model=model,
            max_tokens=150,  # instructor uses tool_use JSON wrapper (~70 tokens overhead); actual output ~105 tokens
            messages=[{"role": "user", "content": user_content}],
            system=PRESCREEN_SYSTEM_PROMPT,
            response_model=_PreScreenResult,
        )
        return result.relevant, result.reason
    except Exception:
        logger.warning(f"pre_screen failed for {raw_job.url!r} — failing open")
        return True, "pre_screen_error"


# ── Client factory ─────────────────────────────────────────────────────────────

def build_client() -> instructor.Instructor:
    return instructor.from_anthropic(
        Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    )


# ── Main extraction function ───────────────────────────────────────────────────

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=5, max=60),
    retry=retry_if_exception_type((RateLimitError, APIConnectionError)),
)
def extract_and_score(raw_job, client: instructor.Instructor) -> "JobPosting":
    """
    Single Claude API call: raw job text → structured JobPosting with relevance score.
    Raises on persistent API failure after 3 retries.
    """
    model = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
    max_chars = int(os.environ.get("RAW_TEXT_MAX_CHARS", 4000))

    user_content = f"""JOB LISTING
Source: {raw_job.source}
URL: {raw_job.url}
Organisation (if known): {raw_job.organization or 'Unknown'}
Location (if known): {raw_job.location or 'Unknown'}

RAW TEXT:
{raw_job.raw_text[:max_chars]}

---
Extract all structured fields and score this job's relevance to Timo's profile.

For the deadline field: extract the application deadline if explicitly stated.
Look for phrases like 'apply by', 'closing date', 'deadline', 'applications close',
'vacancy closes', 'uiterste sollicitatiedatum', 'sluitingsdatum'.
Return as YYYY-MM-DD. If no deadline is found or it is ambiguous, return null."""

    posting = client.chat.completions.create(
        model=model,
        max_tokens=600,
        messages=[{"role": "user", "content": user_content}],
        system=[{"type": "text", "text": _get_full_system_prompt(), "cache_control": {"type": "ephemeral"}}],
        response_model=JobPosting,
    )
    return posting


def safe_extract_and_score(raw_job, client: instructor.Instructor) -> "JobPosting | None":
    """
    Wrapper that catches all exceptions, logs them, returns None on failure.
    Caller is responsible for storing failures to failed_extractions table.
    """
    try:
        return extract_and_score(raw_job, client)
    except Exception:
        logger.exception(f"Extraction failed for {raw_job.url}")
        return None


# ── Deadline backfill ──────────────────────────────────────────────────────────

class _DeadlineOnly(BaseModel):
    deadline: Optional[str] = Field(
        default=None,
        description=(
            "Application deadline as YYYY-MM-DD, or null. "
            "Look for: 'apply by', 'closing date', 'deadline', 'applications close', "
            "'vacancy closes', 'uiterste sollicitatiedatum', 'sluitingsdatum'."
        ),
    )


def _extract_deadline_single(job_id: int, raw_text: str,
                              client: instructor.Instructor) -> tuple[int, Optional[str]]:
    """Extract deadline for one job. Returns (job_id, deadline_str_or_None)."""
    model    = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
    max_chars = int(os.environ.get("RAW_TEXT_MAX_CHARS", 4000))
    try:
        result = client.chat.completions.create(
            model=model,
            max_tokens=60,
            messages=[{
                "role": "user",
                "content": (
                    "Extract the application deadline from this job posting. "
                    "Return YYYY-MM-DD if found, null otherwise.\n\n"
                    f"{raw_text[:max_chars]}"
                ),
            }],
            response_model=_DeadlineOnly,
        )
        return job_id, result.deadline
    except Exception:
        logger.warning(f"Deadline extraction failed for job_id={job_id}")
        return job_id, None


def backfill_deadlines(conn, client: instructor.Instructor) -> int:
    """
    Re-extract deadlines for all jobs where deadline IS NULL.
    Runs up to 5 concurrent API calls to respect rate limits.
    Idempotent — safe to run multiple times.
    Returns count of deadlines successfully filled.
    """
    rows = conn.execute(
        "SELECT id, raw_text FROM jobs WHERE deadline IS NULL AND raw_text IS NOT NULL AND raw_text != ''"
    ).fetchall()

    if not rows:
        logger.info("Backfill: no jobs with missing deadlines found.")
        return 0

    logger.info(f"Backfill: attempting deadline extraction for {len(rows)} jobs...")
    filled = 0

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(_extract_deadline_single, row["id"], row["raw_text"] or "", client): row["id"]
            for row in rows
        }
        for future in as_completed(futures):
            try:
                job_id, deadline = future.result()
                if deadline:
                    conn.execute(
                        "UPDATE jobs SET deadline = ? WHERE id = ? AND deadline IS NULL",
                        (deadline, job_id),
                    )
                    conn.commit()
                    filled += 1
            except Exception:
                logger.exception(f"Unexpected error during backfill for job_id={futures[future]}")

    logger.info(f"Backfill complete: {filled}/{len(rows)} deadlines filled.")
    return filled
