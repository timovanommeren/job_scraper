import os
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
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

def _load_system_prompt() -> str:
    """Load system prompt from config/profile.yaml, falling back to the embedded default."""
    profile_path = os.path.join(os.path.dirname(__file__), "..", "config", "profile.yaml")
    try:
        with open(profile_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("system_prompt", "").strip()
    except Exception:
        logger.warning("Could not load config/profile.yaml; using embedded system prompt")
        return _FALLBACK_SYSTEM_PROMPT


# Embedded fallback so the agent works even if config/profile.yaml is missing
_FALLBACK_SYSTEM_PROMPT = """You are a job relevance screener for Timo van Ommeren.

TIMO'S PROFILE:
- Education: MSc Methodology & Statistics (Utrecht University, ongoing); BSc Psychology cum laude GPA 8.2 (UvA, 2022)
- Thesis: Cold start problem in AI-assisted systematic reviews using ASReview (LLM-based priors for classifier initialisation)
- Technical: R (proficient), Python (intermediate — LLM APIs, data pipelines, ASReview), SPSS, statistical modelling, LaTeX
- Recent experience:
  * ~9-month internship at EMCDDA/EU Drug Agency (EUDA)
  * Junior researcher, UvA Dept of Psychological Methods — drug legalisation attitude research
  * Gemeente Amsterdam "Dealing with Drugs" conference co-organiser
  * Data analyst volunteer, Volt Europa
- Languages: Dutch (native), English (fluent), Portuguese (decent), Spanish (learning)
- Location: Amsterdam; open to temporary relocation including outside Europe
- Target sectors: EU institutions, UN system, think tanks (RAND, BIT, TNI, Busara, CASE Poland), Dutch national research institutes (SCP, WODC, Trimbos)
- Target roles: researcher, data analyst, policy analyst, PhD position, traineeship, methodology specialist
- Strong fit for: drug policy, public health, social/behavioural science, quantitative methods, evidence synthesis, AI/LLM in research

SCORING INSTRUCTIONS:
- Score 8–10: Role directly matches Timo's background + target sector. He could submit a competitive application today.
- Score 5–7: Interesting but one element is missing (wrong sector, slight overqualification, partial language fit).
- Score 1–4: Wrong field, requires qualifications Timo doesn't have, or wrong seniority level.
- Penalise: roles requiring PhD already earned, 5+ years unrelated experience, clinical medical degrees, or hard language requirements.
- Bonus points: Amsterdam-based, EU/UN institutional setting, drug/addiction/public health policy, quantitative methods, statistical modelling, systematic review, AI in research."""

SYSTEM_PROMPT = _load_system_prompt()

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
            max_tokens=250,  # instructor uses tool_use JSON wrapper (~70 tokens overhead)
            messages=[{"role": "user", "content": user_content}],
            system=PRESCREEN_SYSTEM_PROMPT,
            response_model=_PreScreenResult,
        )
        return result.relevant, result.reason
    except Exception:
        logger.warning(f"pre_screen failed for {raw_job.url!r} — failing open")
        return True, "pre_screen_error"


def _get_full_system_prompt() -> str:
    """Return system prompt + any feedback-based calibration additions."""
    try:
        from feedback.profile_updater import generate_prompt_additions
        additions = generate_prompt_additions()
        if additions:
            return SYSTEM_PROMPT + additions
    except Exception:
        pass
    return SYSTEM_PROMPT


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
        system=_get_full_system_prompt(),
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
