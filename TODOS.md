# TODOS

> This file and GitHub Issues are kept in sync — see CLAUDE.md "TODOS.md ↔ GitHub Issues" for the sync protocol.
> Each entry links to its GitHub issue. Issues created remotely should be added here at the next session.

---

## P2 — Blocked scrapers (infrastructure-level, not code bugs)

> Read the full GitHub issue before attempting any fix — these were disabled for reasons that code changes alone cannot resolve.

### TNI: 429 on every run — IP-level block · [#1](https://github.com/timovanommeren/job_scraper/issues/1)

Amsterdam-based think tank with a drug policy programme. Server-side IP rate limit blocks the scraper before headers are evaluated. Reduced retries (2 instead of 5), realistic headers, and jitter all tried — no effect. **Next steps:** check for RSS feed at `tni.org/rss`; inspect DevTools for a Drupal JSON views endpoint; or remove and add to manual weekly check list.

---

### UN Careers: CloudFront 403 — CDN blocks all automation · [#2](https://github.com/timovanommeren/job_scraper/issues/2)

Covers UNDP, UNODC, UNESCO, and other UN agencies. Block occurs at CDN layer before any content loads — selector fixes are irrelevant. **Next steps:** find the REST API backing the search UI (DevTools → Network tab); try INSPIRA portal (`inspira.un.org`); or rely on Impactpool which aggregates UN jobs.

---

### OECD: Cloudflare bot challenge · [#3](https://github.com/timovanommeren/job_scraper/issues/3)

High-priority target (policy, social science, Paris). Cloudflare challenge fires before content loads — Playwright and requests both blocked. **Next steps:** check for RSS/API (`oecd.org/careers/rss`); try `playwright-stealth` package; or check the `erecruit.oecd.org` sub-domain for lighter bot protection.

---

### BIT: Cloudflare block + currently 0 positions · [#4](https://github.com/timovanommeren/job_scraper/issues/4)

Behavioural Insights Team — relevant org but 0 open positions at time of audit (2026-05-28). **Next steps:** test `boards-api.greenhouse.io/v1/boards/behaviouralinsightsteam/jobs` (Greenhouse ATS, typically bot-protection-free); or check manually every 2–3 months rather than maintaining a scraper.

---

### AcademicTransfer: not all Dutch PhDs included · [#8](https://github.com/timovanommeren/job_scraper/issues/8)

Some Dutch university PhD positions are posted directly on university sites and not aggregated by AcademicTransfer. **Next steps:** identify which universities post jobs independently and whether individual university scrapers are worth adding.

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
