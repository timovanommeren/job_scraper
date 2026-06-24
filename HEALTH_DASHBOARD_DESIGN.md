# Design Doc — Health Dashboard

> Status: APPROVED (design only — not yet implemented)
> Date: 2026-06-24
> Author: Timo van Ommeren (via office-hours session)
> Mode: Builder (personal job-hunt pipeline)

---

## Problem

Some mornings an email arrives, some mornings it doesn't, and there is no way to
tell *why*. Was no scrape run at all? Did it run but find nothing strong? Did a
scraper or the API break silently? Right now the only answer is digging through
`logs/scraper.log` or querying SQLite by hand. The uncertainty is the pain.

There is also no longer-term view: what kinds of jobs are actually being found,
whether they are all PhD positions, which sources earn their keep, how much the
Anthropic API is costing, and how engaged the feedback loop is.

## Goal

A single read-only page at `http://localhost:5001/dashboard` with two sections:

1. **Last-run health panel** — an at-a-glance verdict that answers "did it work,
   and if no email, why not?"
2. **Last-30-days analytics panel** — trends and patterns.

Plus a `/api/v1/stats` JSON endpoint exposing the same numbers, so they can be
reused later (weekly email summary, phone widget, future tooling) without
re-querying the database.

## Decided approach (Option 3)

- New read-only route on the **existing always-on Flask server**. No new process,
  no new Task Scheduler entry.
- Rendered with `render_template_string` + inline CSS, exactly like the rest of
  the app. **No JavaScript, no CDN dependency** — fully self-contained.
- Trends drawn as **plain CSS bar rows** (a styled `<div>` whose width = a
  percentage). Crude but dependency-free and offline-safe.
- A shared **stats-computation module** feeds *both* the HTML page and the JSON
  endpoint, so the logic is written once (this is the whole point of Option 3:
  reusability).
- **Token/cost tracking is in scope** — the one piece that needs new
  instrumentation, since `response.usage` is currently never read or stored.

Pretty charts (Chart.js) and proactive alerting are explicitly **out of scope**
for v1 (see below).

## Visual design & alignment (added in design review)

The dashboard is **App UI** (an internal ops page), so it follows calm-surface
App UI rules, not landing-page rules. It must read as "another page of this app,"
not a bolt-on.

- **Reuse the existing de-facto design system in `feedback/server.py`** — do not
  invent a new look. Specifically reuse: the dark nav bar (`#1a1a2e`), the
  centered `.wrap` (`max-width:940px`), the white `.card`, the status palette
  (green `#16a34a`/`#dcfce7`, amber `#ea580c`/`#fff7ed`, red `#dc2626`/`#fef2f2`,
  slate `#64748b`/`#f1f5f9`, primary blue `#2563eb`), and the `.tier-*` pill style.
- **Add a `Dashboard` link to the nav bar** on every page (wayfinding — currently
  nothing routes you there).
- **One prominent verdict card, then a calm table.** The verdict banner is the
  single visually dominant element (largest text, full-width card, status colour).
  Everything below it — timing, counts, errors, per-source yields — is a quiet
  definition-list / table in the existing table style, **not** a grid of
  equal-weight tiles (App UI rules reject the dashboard-card mosaic).
- **Status is a text + colour pill, never emoji or colour alone** (accessibility:
  colourblind + screen-reader safe; also avoids the AI-slop emoji-dot pattern).
- **Light mobile-friendliness.** The single-column `.wrap` already reflows; ensure
  wide tables stack or scroll horizontally on narrow screens and the verdict text
  stays large. No separate mobile layout — you mostly open this on desktop, but a
  quick phone glance should not overflow.

---

## Section 1 — Last-run health panel

All derivable from the most recent `run_log` row plus a couple of small queries.
The verdict is **computed in Python, not stored** — no schema change needed for
the diagnosis itself.

**Verdict banner (the headline — one prominent `.card`, largest text on the page,
status shown as a text+colour pill using the existing palette, never emoji):**

| Pill (colour) | Trigger | Message example |
|---|---|---|
| `SENT` (green) | `status=success`, `jobs_emailed > 0` | "Ran 07:02, finished clean. 3 strong matches → email sent." |
| `NO EMAIL` (slate) | `status=success`, `new_jobs_found > 0`, `jobs_emailed = 0` | "Ran 07:02, finished clean. 14 new jobs, none cleared the threshold (≥6). Working as intended." |
| `NOTHING NEW` (slate) | `status=success`, `new_jobs_found = 0` | "Ran 07:02. Nothing new on any source today." |
| `PARTIAL` (amber) | `status=partial` | "Ran with errors: 2 API errors, 1 scraper failed. Email sent for 1 strong match." |
| `FAILED` (red) | `status=failed`, OR **no run since the last scheduled time** (see boundary logic below) | "Last run FAILED" / "No run recorded since today's 07:00 — the scrape may not have fired." |
| `CRASHED` (red) | latest row has `started_at` but `finished_at IS NULL` and `started_at` is older than a max-run-duration guard (so it can't still be running) | "Run started 07:02 but never finished — likely crashed or killed." |
| `NO NETWORK` (blue) | `status=no_network` | "Skipped — no network at run time." |
| `NEXT RUN` (slate) | before today's scheduled time, no run yet today | "Next scrape at 07:00." (neutral — not a failure) |

Colour is always paired with the pill text and the message, so status never
depends on colour vision alone.

**"Did it run?" boundary logic (schedule-aware — revised in eng review).** Do NOT
use "no row dated today = FAILED": that shows a false FAILED every morning before
the 07:00 scrape. Instead, compare against the scheduled run time (kept in
`settings.yaml`):
- Before today's scheduled time, with no run yet today → `NEXT RUN` (neutral).
- After the scheduled time, with no run since it → `FAILED` ("scrape didn't fire").
- Latest row half-written (`finished_at IS NULL`, started long enough ago) → `CRASHED`.
- All time comparisons in **local time** (`started_at` is UTC-stored; convert).
The "missed/crashed" path is the most important signal — it is the only way to know
the scheduled task itself didn't fire or died mid-run, which no in-app log captures.

**Detail (a calm table / definition-list below the banner — NOT a grid of tiles):**

- Run start time + duration (`started_at` → `finished_at`)
- Sites scraped vs. sites expected (flag if fewer than the registry count)
- New jobs found / scored / filtered (pre-screen rejects)
- Strong matches (score ≥ `strong_match_threshold`) / jobs emailed
- API errors / pre-screen errors
- Tokens used + estimated € cost for this run
- **Per-source yield table** (from `source_yields` JSON): each scraper and its
  count, with 0-yield sources highlighted — but **disabled and seasonal sources
  labelled as such** so an expected 0 doesn't look like a failure.

### Interaction / empty states (added in design review)

Every panel must design its "no data yet" case — these are features, not blanks.

| Surface | Empty / first-run state |
|---|---|
| Verdict banner | No `run_log` rows at all → neutral slate pill `NO RUNS YET` + "The first scrape runs daily at 07:00. Nothing has run yet." |
| Last-run detail table | No rows → hidden; the banner empty state stands alone. |
| Per-source yields | Row exists but `source_yields` empty/missing (older runs) → "Per-source breakdown not recorded for this run." |
| Token / cost tiles | No token data captured yet (pre-instrumentation runs) → "Cost tracking starts from the next run." not "€0.00". |
| 30-day analytics | < 30 days of history → show what exists with a note: "Showing 7 of 30 days — analytics fill in as runs accumulate." |
| Feedback engagement | No ratings in window → "No ratings yet this month." |
| Upcoming deadlines | None in next 14 days → "No deadlines in the next 14 days." |

---

## Section 2 — Last-30-days analytics panel

Hierarchy: lead with the **three things that matter most** — run success rate (is
it healthy?), strong matches found, and API spend. The rest is supporting detail
below them.

- **Runs:** total, success / partial / failed counts, success rate, and a
  **missed-day flag** (calendar days in the window with no run row).
- **Jobs:** total new, total strong, strong-match rate.
- **"Were they all PhDs?"** — breakdown of strong matches by `contract_type`
  and/or `tags` (PhD vs traineeship vs research/analyst), as CSS bars. This is
  the question you explicitly wanted answered.
- **Source value:** top sources over 30 days — not just raw volume but how many
  *strong* matches each produced (which sources actually earn their slot).
- **Score distribution:** histogram of `relevance_score` as CSS bars.
- **Feedback engagement:** ratings received in the last 30 days, average rating,
  and job-detail views (`job_views`) — your own engagement with the loop.
- **API spend:** total tokens + € for the window, a per-day cost bar trend, and
  average cost per job scored.
- **Upcoming deadlines (bonus):** count of jobs whose `deadline` falls in the
  next 14 days — a small actionable extra.

---

## Section 3 — JSON endpoint

`GET /api/v1/stats` → returns `{ "last_run": {...}, "last_30_days": {...} }`,
mirroring the two panels. Localhost-bound and read-only, so no auth (consistent
with `/jobs` and `/health`). If ever exposed through the Cloudflare tunnel it
should adopt the same `Authorization: Bearer <CF_WORKER_SECRET>` guard the other
`/api/v1/*` routes use.

---

## Token / cost instrumentation (the only non-free piece)

> **De-risk first (eng review):** before building the rest of this section, run a
> 5-minute spike — call `client.chat.completions.create_with_completion(...)` once
> against the real Haiku model and print `completion.usage`. Confirm the field
> names (input/output/cache) before wiring all three call sites. This was the
> plan's one genuine unknown; resolve it before building on it.

1. **Schema:** add columns to `run_log` via `db/migrations.py:_safe_add_column()`
   (never `DROP`/recreate): `input_tokens`, `output_tokens`, `cache_read_tokens`,
   `cache_creation_tokens` (all `INTEGER DEFAULT 0`), `est_cost_eur` (`REAL
   DEFAULT 0`).
2. **Capture:** in `agents/extractor_scorer.py` (the *only* file allowed to call
   the API), switch the calls in `extract_and_score()`, `pre_screen()` (and the
   deadline extractor) from `create(...)` to **`create_with_completion(...)`**,
   which returns `(parsed_model, raw_completion)`; read `raw_completion.usage`.
   (Resolved: `instructor` exposes usage via this sibling method — confirm exact
   field names in the spike above.)
3. **Aggregate (return-per-call, sum in caller):** each function returns its usage
   alongside its result; `score_new_jobs()` sums them in its existing **sequential**
   `for` loop (`main.py:175` — no threading, so this is thread-safe by
   construction). Do NOT use a module-level accumulator (shared mutable state, and
   the concurrent `backfill_deadlines` path would race it).
4. **Cost:** pricing map in `config/settings.yaml` keyed by model name (not
   hardcoded in logic, per the NEVER rules) — input/output/cache per-million-token
   rates + a EUR/USD factor. Compute `est_cost_eur` at run finish.
   **Unknown-model fallback:** if the active `CLAUDE_MODEL` isn't in the pricing
   map, store the token counts but leave `est_cost_eur` null and have the tile show
   "pricing not configured for `<model>`" — never crash the run or the dashboard.
5. **Write:** extend the `log_run_finish()` writer to persist the new fields
   (add keyword params with defaults — keeps the existing `no_network` call site
   and any other caller working).

> **Ship order (eng review, Step 0):** the dashboard read-path (T1–T4, T6) touches
> only existing data and is low-risk; the token instrumentation (T5) is the only
> part touching a migration + the LLM call path. They split cleanly into two PRs —
> dashboard first as a standalone win, instrumentation second. (Token tracking
> stays in scope; this is sequencing, not a cut.)

Historical cost **cannot be backfilled** — it was never captured. The 30-day
spend tile starts filling from the first run after this ships.

---

## Files to touch

| File | Change |
|---|---|
| `feedback/server.py` | New `/dashboard` route + `/api/v1/stats` route; add `Dashboard` link to the shared nav bar; **call idempotent `init_db()` at startup** so restarting Flask applies pending migrations |
| `feedback/stats.py` (**new**) | `compute_stats(conn) -> dict` — shared by both routes; builds on (does not duplicate) `db/dedup.py:get_run_stats()` for run_log aggregation |
| `db/dedup.py` | **Extend `get_run_stats()`** to be the single source of run_log aggregation (reused by the weekly digest AND the dashboard); extend `log_run_finish()` signature for token fields (keyword params, defaults) |
| `db/migrations.py` | Add 5 token/cost columns to `run_log` |
| `agents/extractor_scorer.py` | Switch 3 call sites to `create_with_completion()`, return `usage` per call |
| `main.py` | Sum per-call usage in the sequential `score_new_jobs` loop, compute cost, pass to `log_run_finish()` |
| `config/settings.yaml` | Pricing map (keyed by model) + dashboard window (30 days) + scheduled run time (for the verdict boundary) |
| `tests/test_stats.py` (**new**) | Unit tests for `compute_stats` / verdict states / cost fn (see Testing) |
| `tests/test_dashboard_routes.py` (**new**) | Flask smoke tests for `/dashboard` + `/api/v1/stats` |
| `CLAUDE.md`, `ARCHITECTURE.md`, `README.md` | **Required** — new route + schema change trips the doc-update rule |

---

## Testing (added in eng review — full coverage)

Match the existing pytest suite (`tests/test_*.py`; see `test_flask_tags.py` for
Flask, `test_content_hash_dedup.py` for DB fixtures). `compute_stats(conn) -> dict`
is pure (connection in, dict out), so it's cheap to test against an in-memory /
fixture DB.

- **Verdict states (one test each):** `SENT`, `NO EMAIL`, `NOTHING NEW`, `PARTIAL`,
  `FAILED`, `NO NETWORK`, `NEXT RUN`, `CRASHED` (row with `finished_at IS NULL`),
  `NO RUNS YET` (empty `run_log`), and the schedule-aware boundary (before vs after
  the scheduled time).
- **Cost function:** known model → expected euro; **unknown model → null cost, no
  crash** (the fallback path).
- **Token summation:** per-call usage sums correctly across a multi-job run.
- **30-day aggregates:** contract_type breakdown, empty-window (<30 days) note.
- **CRITICAL regression test:** extending `get_run_stats()` must not break the
  weekly digest path — assert the weekly digest still computes correctly against
  the same fixture. (Iron rule: regression test is mandatory, no opt-out.)
- **Flask smoke:** `GET /dashboard` → 200 and renders with an **empty DB** (first-run
  must not 500); `GET /api/v1/stats` → expected JSON shape.

---

## Out of scope (v2 candidates)

- **Chart.js / pretty graphs** — deliberately skipped; CSS bars are enough.
- **Proactive alerting** — the dashboard is passive; you still have to remember
  to open it. The highest-value follow-up is a one-line status in the *daily
  email itself* (sent even when there are no strong jobs: "Ran clean, nothing
  strong today") and/or a heartbeat that pings you when a run is *missed*. This
  attacks the root anxiety better than any page, and the `/api/v1/stats` endpoint
  built here makes it cheap. Strongly recommended as the next thing.
- **Backfilling historical token cost** — impossible; not captured.

---

## What already exists (reuse, don't reinvent)

The dashboard is built almost entirely from primitives already in the codebase:

- **Visual system** in `feedback/server.py`: nav bar, `.wrap`, `.card`, status
  colour palette, `.tier-*` pills, existing `table` styling. The dashboard adds
  no new design vocabulary.
- **All last-run + analytics data** already lives in `run_log`, `jobs`, `feedback`,
  and `job_views`. Only token/cost is new.
- **`render_template_string`** pattern and `_get_db()` per-request connection —
  the new routes follow the existing route conventions exactly.
- **`/api/v1/*` route pattern** (e.g. `/api/v1/feedback`) — the stats endpoint
  mirrors it.

## NOT in scope — design decisions explicitly deferred

- **Chart.js / animated charts** — CSS bars chosen instead (offline-safe, no CDN).
- **Full per-viewport responsive design** — only light mobile-friendliness; this
  is a desktop-first single-user tool.
- **Real-time / auto-refresh** — the page reflects the last run on load; no live
  polling. Refresh by reloading.
- **Configurable analytics window** — fixed at 30 days for v1 (the value lives in
  `settings.yaml` so it can change later without code edits).

---

## The assignment (do this before implementing)

Open `db/jobs.db` in SQLite and run:

```sql
SELECT started_at, finished_at, status, sites_scraped,
       new_jobs_found, jobs_scored, jobs_emailed, api_errors
FROM run_log
ORDER BY started_at DESC
LIMIT 7;
```

Two payoffs: (1) you immediately learn whether the scrape has actually been
firing every morning — which is the exact anxiety this whole project addresses —
and (2) you see the raw material the health panel will render, so when we build
it you already know what "healthy" looks like and can sanity-check the verdict
logic against real rows.

---

## Approved wireframe

Real-CSS wireframe (reuses the live app styles, static demo data):
`~/.gstack/projects/timovanommeren-job_scraper/designs/health-dashboard-20260624/wireframe.html`
Direction: verdict card + calm detail table, colour+text pills, CSS bars, 30-day
top-3 KPIs. This is the visual reference to build from.

## Implementation Tasks

> **PR 1 SHIPPED (2026-06-24):** the read-only dashboard. Done: T1, T2, T3, T4,
> T6, T7, T8, and E2, E3, E4 (DRY via reuse of `get_run_stats`), E7, E8 (21 tests,
> full suite 180 passing). New: `feedback/stats.py`, `tests/test_stats.py`,
> `tests/test_dashboard_routes.py`; `config/settings.yaml:dashboard`; `/dashboard`
> + `/api/v1/stats` routes; `Dashboard` nav link; `init_db()` at Flask startup.
> **PR 2 SHIPPED (2026-06-24):** token/cost instrumentation. E1 spike confirmed
> `create_with_completion().usage` (fields: input/output/cache_read_input/
> cache_creation_input; `inference_geo='not_available'` so caching still inactive
> on Haiku 4.5, as known). T5/E5: 5 token columns on `run_log` via
> `_safe_add_column`; `extract_and_score`/`pre_screen` switched to
> `create_with_completion` and append usage to a caller-owned sink; `main.py` sums
> and stores per run. E6: pricing map in `config/settings.yaml:dashboard.pricing`,
> `compute_cost_eur()` with unknown-model fallback. Dashboard spend tile + last-run
> tokens now live. +4 tests (cost known/unknown, spend aggregate, usage capture);
> full suite 184 passing. Spend shows "no data yet" until the next pipeline run
> writes token totals.

Synthesized from the design review. Each derives from a finding above.

- [ ] **T1 (P1, human: ~3h / CC: ~25min)** — dashboard — Add `/dashboard` route + shared `compute_stats()` module
  - Files: `feedback/server.py`, `feedback/stats.py`
- [ ] **T2 (P1, human: ~30min / CC: ~10min)** — design-system — Reuse existing CSS system + add `Dashboard` nav link (Pass 5)
  - Files: `feedback/server.py`
- [ ] **T3 (P1, human: ~1h / CC: ~15min)** — layout — Verdict card + calm detail table; colour+text pills (6 states), no emoji (Pass 1/4/5)
  - Files: `feedback/server.py`
- [ ] **T4 (P1, human: ~1h / CC: ~15min)** — states — Design empty/first-run states for every panel (Pass 2)
  - Files: `feedback/server.py`, `feedback/stats.py`
- [ ] **T5 (P2, human: ~2h / CC: ~20min)** — instrumentation — Token/cost capture: `run_log` columns + `response.usage` in extractor + aggregate + pricing in settings
  - Files: `db/migrations.py`, `agents/extractor_scorer.py`, `main.py`, `db/dedup.py`, `config/settings.yaml`
- [ ] **T6 (P2, human: ~45min / CC: ~10min)** — api — Add `/api/v1/stats` JSON endpoint sharing the stats module
  - Files: `feedback/server.py`
- [ ] **T7 (P2, human: ~30min / CC: ~10min)** — responsive — Light mobile-friendliness: tables stack/scroll, verdict stays large (Pass 6)
  - Files: `feedback/server.py`
- [ ] **T8 (P2, human: ~30min / CC: ~10min)** — docs — Update CLAUDE.md / ARCHITECTURE.md / README.md (NEVER rule #11)
  - Files: `CLAUDE.md`, `ARCHITECTURE.md`, `README.md`

### Eng-review tasks (do these too — most are P1)

- [ ] **E1 (P1)** — Spike: print `create_with_completion().usage` for one real Haiku call before building T5 — `agents/extractor_scorer.py`
- [ ] **E2 (P1)** — Schedule-aware verdict boundary (NEXT RUN before scheduled time, FAILED after; local time) — `feedback/stats.py`, `config/settings.yaml`
- [ ] **E3 (P1)** — Add CRASHED state (`finished_at IS NULL`, started long ago) — `feedback/stats.py`
- [ ] **E4 (P1)** — Extend `get_run_stats()` as single run_log aggregation source; `stats.py` builds on it — `db/dedup.py`, `feedback/stats.py`
- [ ] **E5 (P1)** — Switch 3 call sites to `create_with_completion`, return usage per call, sum in sequential loop — `agents/extractor_scorer.py`, `main.py`
- [ ] **E6 (P1)** — Pricing map keyed by model + unknown-model fallback (null cost, no crash) — `config/settings.yaml`, `feedback/stats.py`
- [ ] **E7 (P1)** — `init_db()` at Flask startup; document restart-after-migrate — `feedback/server.py`
- [ ] **E8 (P1)** — Full tests: verdict states, cost fn (known+unknown), token sum, **weekly-digest regression test**, Flask empty-DB smoke — `tests/test_stats.py`, `tests/test_dashboard_routes.py`

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | not run |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | not run |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | clean | 10 issues, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 1 | clean | score: 6/10 → 9/10, 5 decisions |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | not run |

- **CROSS-MODEL:** Outside voice (Claude subagent, Codex unavailable) ran. Two claims rebutted with code evidence (scoring loop is sequential → thread-safe; WAL handles concurrent read). Three accepted into the plan: schedule-aware verdict boundary (superseded the calendar-day choice), the CRASHED half-written-row state, and verified-real status vocabulary. Cost-deferral tension resolved by keeping token tracking in scope with a two-PR ship order.
- **VERDICT:** DESIGN + ENG CLEARED — ready to implement. Eng review surfaced 10 findings, all folded into the plan; 0 critical gaps; full test coverage specified.

NO UNRESOLVED DECISIONS
