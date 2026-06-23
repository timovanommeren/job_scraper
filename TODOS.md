# TODOS

> This file and GitHub Issues are kept in sync — see CLAUDE.md "TODOS.md ↔ GitHub Issues" for the sync protocol.
> Each entry links to its GitHub issue. Issues created remotely should be added here at the next session.

---

## P2 — Blocked scrapers (infrastructure-level, not code bugs)

> Read the full GitHub issue before attempting any fix — these were disabled for reasons that code changes alone cannot resolve.

### UN Careers: CloudFront 403 — CDN blocks all automation · [#2](https://github.com/timovanommeren/job_scraper/issues/2)

Covers UNDP, UNODC, UNESCO, and other UN agencies. Block occurs at CDN layer before any content loads — selector fixes are irrelevant. **Next steps:** find the REST API backing the search UI (DevTools → Network tab); try INSPIRA portal (`inspira.un.org`); or rely on Impactpool which aggregates UN jobs.

---

### EUDA: Cloudflare blocks all automation · [#23](https://github.com/timovanommeren/job_scraper/issues/23)

EUDA (European Union Drugs Agency) is the highest-relevance employer for Timo's profile but is fully Cloudflare-gated. Probed 2026-06-23: every path on `euda.europa.eu` (jobs, calls, sitemap, root) returns HTTP 403 with a "Just a moment" JS challenge; `e-recruitment.euda.europa.eu` same; headless Playwright also fails to clear the challenge. `scrapers/euda.py` is a disabled stub returning `[]`. **Next steps (re-probe periodically — OECD/BIT precedent shows blocks can be stale):** try a headful or residential-IP browser; check whether EU Careers / EURAXESS already cross-post EUDA traineeships & SNE calls; watch for a public JSON/API endpoint behind the Cloudflare-gated HTML (as OECD's SmartRecruiters API turned out to be).

---

> **Resolved 2026-06-22:** OECD ([#3](https://github.com/timovanommeren/job_scraper/issues/3)) re-enabled via SmartRecruiters Posting API; BIT ([#4](https://github.com/timovanommeren/job_scraper/issues/4)) re-enabled via WordPress careers page; TNI ([#1](https://github.com/timovanommeren/job_scraper/issues/1)) disabled wontfix (IP-level 429 hits even RSS) and moved to the manual weekly check list. In both re-enabled cases the "Cloudflare block" only applied to a stale HTML entry point — re-probe such sources periodically.

---

### AcademicTransfer: not all Dutch PhDs included · [#8](https://github.com/timovanommeren/job_scraper/issues/8)

Some Dutch university PhD positions are posted directly on university sites and not aggregated by AcademicTransfer. **Resolved (2026-05-31):** 7 Dutch university scrapers added and portal-audited — all confirmed returning live jobs: uu (24), eur (24), radboud (13), tilburg (10–18), uva (10), vu (10), rug (~12). **Remaining limitation:** RUG only captures ~12 positions per run (first hash-pagination page); later pages are hidden after go_back() due to WordPress hash-paging. Lower-priority since RUG re-posts positions and the most recent ones are captured.

---

## P2 — Known limitation: Haiku 4.5 prompt caching not available

### Prompt caching blocked on Haiku 4.5 · [#19](https://github.com/timovanommeren/job_scraper/issues/19)

**What:** `extract_and_score` already sends the system prompt in list-form with `cache_control: {"type": "ephemeral"}` (shipped 2026-05-31, commit `ef3228b`). The format is correct. Caching does not trigger.

**Why blocked:** `claude-haiku-4-5-20251001` routes through `inference_geo='not_available'` — an infrastructure path that doesn't support prompt caching. Confirmed via `usage.cache_creation_input_tokens=0` even with prompts well above the 2048-token minimum. `claude-sonnet-4-6` confirmed working (`inference_geo='global'`, `ephemeral_5m_input_tokens` populated). Older Haiku models (3.5, 3) return 404 on this account.

**When it unblocks:** When Haiku 4.5 gains caching support, `extract_and_score` will start saving ~52% on input token costs with zero further code changes. Check by running the one-liner: `python -c "from anthropic import Anthropic; c=Anthropic(); r=c.messages.create(model='claude-haiku-4-5-20251001',max_tokens=10,system=[{'type':'text','text':'x'*3000,'cache_control':{'type':'ephemeral'}}],messages=[{'role':'user','content':'hi'}]); print(r.usage.cache_creation_input_tokens)"` — non-zero means caching is live.

**What's already in:** `pre_screen` max_tokens reduced 250→150 (saves ~145 output tokens per call, active now).

---

## P1 — Blocked on conditions being met

### Option C migration: calibrate_threshold.py + embedding pre-filter · [#11](https://github.com/timovanommeren/job_scraper/issues/11)

**What:** Write `scripts/calibrate_threshold.py` and execute the Layer 2 B→C migration.

**Why:** Option B (Claude pre-screen) is the current Layer 2 pre-filter. Option C (sentence-transformers embedding similarity) is the planned long-term replacement. Migration conditions and the full checklist are documented in CLAUDE.md under "Layer 2 Pre-filter: B→C Transition."

**Context:** The migration is a single function body replacement in `agents/extractor_scorer.py:pre_screen()`. The caller interface is unchanged. `calibrate_threshold.py` embeds all jobs in `db/jobs.db`, computes cosine similarity against the profile vector, finds the natural gap between low-scored and high-scored jobs, and writes `T_low` and `T_high` to `config/settings.yaml`. See design doc: `~/.gstack/projects/timovanommeren-job_scraper/timov-main-design-multi-layer-filter-20260530.md` for the full two-threshold explore band spec.

**When to pick this up:** When the weekly health report shows ANY of:
1. `Layer 2 mode: B` AND you are expanding to conferences/funding calls
2. `Weekly API cost` ≥ €5/month (current: ~€0.07/week)
3. `Labeled jobs` ≥ 300

**Effort:** M (human ~1 day / CC ~20 min)

**Depends on:** T1–T7 shipped (Layer 2 B in production), 300+ labeled jobs in DB.

---

## P2 — Deferred from multi-layer filter review (2026-05-30)

### R4: Refine pre-screen prompt — remove org-based clause · [#12](https://github.com/timovanommeren/job_scraper/issues/12)

**What:** The current `PRESCREEN_SYSTEM_PROMPT` includes "research at a policy-oriented organisation" which is too broad (a marine ecologist at OECD passes). Remove this clause and restrict to discipline only: *"quantitative social/behavioural science, public policy research methods, or public health."*

**Why:** Org-based boosting is Layer 3's responsibility (liked_organizations in profile.yaml). The pre-screen prompt should be discipline-only to avoid false passes on irrelevant roles at policy orgs.

**Effort:** XS (~30 min). One constant change + T7 test update.

**Deferred from:** 2026-05-30 multi-layer filter adversarial review (R4)

---

### R5: Define `content_type` prompt dispatch in `pre_screen()` · [#13](https://github.com/timovanommeren/job_scraper/issues/13)

**What:** `pre_screen(raw_job, client, content_type='job')` currently ignores `content_type`. Before the parameter is used for conferences/funding calls, implement a `_PRESCREEN_PROMPTS` dict keyed by content type so `content_type="conference"` doesn't silently fall back to the job prompt.

**Why:** The interface promises pluggability that isn't implemented. First caller using a non-"job" content_type gets wrong results with no error.

**Effort:** XS (~1h). Dict + dispatch + two new test cases.

**Deferred from:** 2026-05-30 multi-layer filter adversarial review (R5)

---

### R6: Live verification gate before T6 (Euraxess Layer 1) · [#14](https://github.com/timovanommeren/job_scraper/issues/14)

**What:** Before editing `scrapers/euraxess.py` for T6, run a live check that `?keywords=social+science` works with existing pagination. Add a comment to the scraper documenting the verified URL pattern and date verified.

**Why:** Euraxess is a protected scraper (CLAUDE.md NEVER Rule #1). Touching it without verification risks breaking the highest-volume working source.

**Effort:** XS (~30 min verification + 1 line comment).

**Deferred from:** 2026-05-30 multi-layer filter adversarial review (R6)

---

### A3: Harden `expires_at` datetime format in `filtered_jobs` · [#15](https://github.com/timovanommeren/job_scraper/issues/15)

**What:** Change `expires_at` storage from Python's `datetime.isoformat()` (T-separator, timezone suffix) to SQLite's native space-separated format so `expires_at > datetime('now')` is a safe same-format string comparison, not an accidentally-correct cross-format comparison.

**Why:** The current comparison works because ASCII `T` > ` `, but this is undocumented and will break if the timestamp format ever changes.

**Effort:** XS. One change in `insert_filtered()` — use `strftime('%Y-%m-%d %H:%M:%S', datetime('now', '+30 days'))` at INSERT time, or store as `filtered_at.replace('T', ' ').split('+')[0][:19]`.

**Deferred from:** 2026-05-30 multi-layer filter adversarial review (A3)

---

### Feedback UX: CF Worker / Flask split · [#10](https://github.com/timovanommeren/job_scraper/issues/10)

**What:** Rethink the feedback architecture — two parallel paths (CF Worker + Flask) that write to the same data model with no clear rationale for which to use when.

**Why:** Demonstrated today: stale Flask server caused silent write failure while still showing "Feedback saved!". The two forms (CF `/rate` and Flask `/jobs/{id}`) are maintained separately and can silently diverge. Needs engineer + designer review.

**Deferred from:** 2026-05-30 session

---

## P2 — Deferred features

### Score drift dashboard · [#16](https://github.com/timovanommeren/job_scraper/issues/16)

Track calibration drift over time: plot Claude's average score per week, flag if scoring distribution shifts significantly. Useful after the feedback loop has run for 2+ months with 50+ ratings.

**Deferred from:** 2026-05-29 CEO plan (feedback loop)

---

### Deadline reminder emails · [#17](https://github.com/timovanommeren/job_scraper/issues/17)

Send a second email N days before a job's deadline if it hasn't been rated yet.

**Deferred from:** 2026-05-29 CEO plan (feedback loop)

---

### Explore panel UX · [#18](https://github.com/timovanommeren/job_scraper/issues/18)

Surface 5 random low-scored (≤3), unviewed jobs in the Flask UI for active labeling.
Data model is already in place (job_views LEFT JOIN). Needs UX design via /plan-design-review.

**Deferred from:** 2026-05-30 multi-layer filter design

---

### Save / read later box · [#9](https://github.com/timovanommeren/job_scraper/issues/9)

Allow interesting positions to be saved in a "read later" box in the Flask UI.

**Deferred from:** 2026-05-30 (created as GitHub issue)

### Weighted criteria scoring · (deferred, no issue yet)

**What:** After accumulating 300+ feedback entries with criterion scores, run a correlation analysis to determine which of the 5 criteria (topic_fit, methods_fit, org_appeal, career_fit, location_fit) best predicts the final `relevance_score`. Add weights to the `round(avg(criteria) × 2)` formula accordingly.

**Why:** The current equal-weight formula (`round(avg(criteria) × 2)`) is arbitrary — all 5 criteria count equally. In practice, topic_fit likely dominates (wrong field = hard pass regardless of other dimensions). Weighted scoring would produce more accurate auto-computed scores.

**How to apply:** Run `scripts/analyze_criteria_weights.py` (to be written) against `db/jobs.db` to correlate each criterion against final `relevance_score`. Use ridge regression or simple Pearson correlation. Write weights to `config/settings.yaml:criteria_weights`. Update `submit_feedback()` formula.

**Deferred from:** 2026-05-31 (feedback form redesign). Trigger condition: 300+ labeled jobs (same threshold as Option C embedding pre-screening migration).
