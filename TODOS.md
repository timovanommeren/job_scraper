# TODOS

## P1 — Blocked on conditions being met

### Option C migration: calibrate_threshold.py + embedding pre-filter

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

### R4: Refine pre-screen prompt — remove org-based clause

**What:** The current `PRESCREEN_SYSTEM_PROMPT` includes "research at a policy-oriented organisation" which is too broad (a marine ecologist at OECD passes). Remove this clause and restrict to discipline only: *"quantitative social/behavioural science, public policy research methods, or public health."*

**Why:** Org-based boosting is Layer 3's responsibility (liked_organizations in profile.yaml). The pre-screen prompt should be discipline-only to avoid false passes on irrelevant roles at policy orgs.

**Effort:** XS (~30 min). One constant change + T7 test update.

**Deferred from:** 2026-05-30 multi-layer filter adversarial review (R4)

---

### R5: Define `content_type` prompt dispatch in `pre_screen()`

**What:** `pre_screen(raw_job, client, content_type='job')` currently ignores `content_type`. Before the parameter is used for conferences/funding calls, implement a `_PRESCREEN_PROMPTS` dict keyed by content type so `content_type="conference"` doesn't silently fall back to the job prompt.

**Why:** The interface promises pluggability that isn't implemented. First caller using a non-"job" content_type gets wrong results with no error.

**Effort:** XS (~1h). Dict + dispatch + two new test cases.

**Deferred from:** 2026-05-30 multi-layer filter adversarial review (R5)

---

### R6: Live verification gate before T6 (Euraxess Layer 1)

**What:** Before editing `scrapers/euraxess.py` for T6, run a live check that `?keywords=social+science` works with existing pagination. Add a comment to the scraper documenting the verified URL pattern and date verified.

**Why:** Euraxess is a protected scraper (CLAUDE.md NEVER Rule #1). Touching it without verification risks breaking the highest-volume working source.

**Effort:** XS (~30 min verification + 1 line comment).

**Deferred from:** 2026-05-30 multi-layer filter adversarial review (R6)

---

### A3: Harden `expires_at` datetime format in `filtered_jobs`

**What:** Change `expires_at` storage from Python's `datetime.isoformat()` (T-separator, timezone suffix) to SQLite's native space-separated format so `expires_at > datetime('now')` is a safe same-format string comparison, not an accidentally-correct cross-format comparison.

**Why:** The current comparison works because ASCII `T` > ` `, but this is undocumented and will break if the timestamp format ever changes.

**Effort:** XS. One change in `insert_filtered()` — use `strftime('%Y-%m-%d %H:%M:%S', datetime('now', '+30 days'))` at INSERT time, or store as `filtered_at.replace('T', ' ').split('+')[0][:19]`.

**Deferred from:** 2026-05-30 multi-layer filter adversarial review (A3)

---

### GitHub issues integration

**What:** After generating TODOs in TODOS.md, automatically create matching GitHub issues via `gh issue create`. Currently blocked because tokens need `public_repo` scope. When creating a new token, check `public_repo` under "repo", then run `! gh auth login --with-token <<< "token"` and fire the queued `gh issue create` calls.

**Deferred from:** 2026-05-30 review session

---

## P2 — Deferred features

### Score drift dashboard
Track calibration drift over time: plot Claude's average score per week, flag if scoring distribution shifts significantly. Useful after the feedback loop has run for 2+ months with 50+ ratings.
**Deferred from:** 2026-05-29 CEO plan (feedback loop)

### Deadline reminder emails
Send a second email N days before a job's deadline if it hasn't been rated yet.
**Deferred from:** 2026-05-29 CEO plan (feedback loop)

### Explore panel UX
Surface 5 random low-scored (≤3), unviewed jobs in the Flask UI for active labeling.
Data model is already in place (job_views LEFT JOIN). Needs UX design via /plan-design-review.
**Deferred from:** 2026-05-30 multi-layer filter design
