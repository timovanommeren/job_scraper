# Plan: Feedback Architecture — Flask Primary, CF Worker as Thin Extension

Closes: [#10](https://github.com/timovanommeren/job_scraper/issues/10)
Branch: main
Status: SHIPPED

## Problem

The feedback system has two parallel paths that both write to the same data model but
through completely different stacks, with no clear rationale for which to use when.
When the criteria sliders shipped in Flask (commit `e3f8ea6`), they were never ported
to the CF Worker `/rate` form. Every phone rating has been missing criteria data —
the primary training signal for per-dimension score calibration — since that date.

The CF Worker `/rate` form also has a different UI (1 score slider + tag pills) than
Flask (5 criteria sliders). Two forms claiming to do the same thing but producing
different data shapes. This will keep diverging.

## Decision

**Flask is the primary feedback system.** It owns the CRITERIA definition, the form
UX, the SQLite write, and the JSON store write. It is the source of truth for all
feedback data.

**CF Worker is a thin, specific extension of Flask** — its only job is to serve the
SAME survey form on mobile (where localhost:5001 is unreachable), store the response in
KV, and let cf_sync.py pull it back. The Worker has no independent opinion about what
the form looks like or what data it collects. It mirrors Flask exactly.

## Architecture

```
EMAIL PILL (1–10)
  └─ GET CF /feedback?score=N&sig=...
     └─ KV: {job_id, action, score, ts}
        └─ cf_sync.py (hourly) → SQLite: relevance_score only
           └─ [no criteria — quick rating, no form shown]

EMAIL FULL SURVEY
  └─ GET CF /survey?job_id=X&sig=...    ← replaces /rate
     └─ serve mobile survey form (identical to Flask criteria sliders)
        └─ POST CF /survey
           └─ KV: {job_id, action, criteria:{...}, comment, derived_score, ts}
              └─ cf_sync.py (hourly) → SQLite: criteria + relevance_score

FLASK (desktop / tablet)
  └─ GET /jobs/<id>    → job detail + criteria slider form
     └─ POST /jobs/<id>/feedback → SQLite + JSON store (IMMEDIATE, no sync lag)
```

## Single Source of Truth: CRITERIA

`CRITERIA` lives in **`config/criteria.yaml`** — the authoritative list. Both Flask
and the CF Worker generator read from there.

```yaml
# config/criteria.yaml
criteria:
  - key: topic_fit
    label: "Topic relevance"
    hint_low: "Wrong research area"
    hint_high: "Core interest area"
  - key: methods_fit
    label: "Methods match"
    hint_low: "Methods I don't use"
    hint_high: "Perfect methods match"
  - key: org_appeal
    label: "Organization appeal"
    hint_low: "Not interested in this org"
    hint_high: "Dream organization"
  - key: career_fit
    label: "Career stage fit"
    hint_low: "Wrong level (e.g. postdoc)"
    hint_high: "Perfect career stage"
  - key: location_fit
    label: "Location"
    hint_low: "Outside EU / unacceptable"
    hint_high: "Ideal location"
```

Flask reads `config/criteria.yaml` at startup; the `CRITERIA` constant in `server.py`
becomes a loader call. A `scripts/generate_worker_form.py` script reads the same YAML
and regenerates the survey form HTML in `cloudflare/worker/index.js` — run as part of
`wrangler deploy` preparation.

**Rule: never edit the form HTML in `index.js` directly.** Edit `config/criteria.yaml`
and re-run the generator.

## Email Link Copy Changes

The job card in emails has two action links that currently feel similar:
- `→ Apply` — opens the external job listing URL (the actual job page)
- `Full feedback (tags + note) →` — opens the CF Worker survey form

Rename:
- `→ Apply` → `→ View job` (in `notifier/gmail.py:241`)
- `Full feedback (tags + note) →` → `→ Rate in detail` (removes stale "tags" reference; matches new form)

The "Applied" action override remains inside the survey form itself (checkbox row as chosen).

## Files Changed

| File | Change |
|---|---|
| `config/criteria.yaml` | NEW — authoritative CRITERIA definition |
| `feedback/server.py` | Load CRITERIA from `config/criteria.yaml` instead of inline constant |
| `cloudflare/worker/index.js` | Replace `/rate` with `/survey`; form HTML generated from YAML |
| `scripts/generate_worker_form.py` | NEW — reads criteria.yaml, writes survey form HTML into index.js |
| `feedback/cf_sync.py` | Parse `criteria` dict from KV entries; write to SQLite `criteria` column |
| `notifier/gmail.py` | `_feedback_action_url(jid, "rate")` → `_feedback_action_url(jid, "survey")` for full form link |
| `db/dedup.py` / `db/migrations.py` | No schema change needed — `criteria` column already exists |

## Mobile Form: Job Context Display

The GET `/survey` URL includes `title` and `org` as URL-encoded query params.
The Worker reads them and injects them into the form header HTML. They are NOT
included in the HMAC payload (signing only job_id + action + bucket is sufficient).

`gmail.py:_feedback_action_url()` adds `title=...&org=...` params when generating
the `/survey` link. URL-encode with `urllib.parse.quote_plus`. Max length: use
`title[:60]` to keep URLs reasonable.

Example URL:
```
https://worker.dev/survey?job_id=123&sig=abc&title=PhD+Researcher&org=WODC
```

## HMAC / Route Changes

- Email pill taps: action="rate", route `/feedback` — **unchanged**
- Email full survey: action changes "rate" → "survey", route `/rate` → `/survey`
- Old `/rate` route kept for backward-compat (redirect to `/survey` with same params)
- New emails use `/survey` signed with action="survey"

No HMAC secret rotation needed — just a new action string.

## KV Envelope Schema (new /survey POST)

```json
{
  "job_id": "123",
  "action": "like",
  "derived_score": 7,
  "criteria": {
    "topic_fit": 4,
    "methods_fit": 3,
    "org_appeal": 4,
    "career_fit": 5,
    "location_fit": 3
  },
  "comment": "Looks like a good fit for my PhD",
  "ts": "2026-06-04T08:00:00.000Z"
}
```

`derived_score` is computed by the Worker: `round(avg(criteria_values) / 5 * 10)` —
same formula as Flask (`round(sum(criteria.values()) / len(criteria) * 2)`).

## Bug Fixes (independent of architecture)

**1. Silent "Feedback saved!" on write failure (Flask)**
`_write_feedback_sqlite` in `server.py` catches exceptions and continues.
Fix: return a `bool` success flag; on `False`, redirect to job detail with an error
flash message instead of the "feedback saved" banner.

**2. cf_sync.py: write criteria when present**
Currently writes only `relevance_score`. New: if `criteria` key is present in KV
entry, write it to the `criteria` column in SQLite too, and use `derived_score` as
`relevance_score`.

## Migration

- All existing KV entries with `score`-only shape (from old /rate form) continue to
  sync correctly — cf_sync.py treats missing `criteria` as `None` (existing behavior).
- Old `/rate` signed links in already-sent emails redirect to `/survey` — the HMAC
  check for action="rate" still passes on the redirect.

## Mobile Form: Accessibility & Touch Targets

- Slider thumb: 32px diameter, with 12px vertical padding on the `input[type=range]` for 44px effective touch zone (Apple HIG minimum)
- Submit button: 48px height minimum
- "Applied" checkbox: 20px visual, wrapped in a label element covering the full row (44px height)
- Viewport: `<meta name="viewport" content="width=device-width,initial-scale=1">` — already present in current Worker HTML
- ARIA: slider `aria-label` = criterion label text; `aria-valuemin/max/now` on each range input
- Color contrast: hint text (#9ca3af on white) is 2.85:1 — below 4.5:1 WCAG AA for small text. Fix: use #6b7280 for hint text (4.48:1). Criterion labels (#374151) and submit button (#fff on #2563eb) both pass.

## Interaction States

| Surface | State | User sees |
|---|---|---|
| GET /survey | Valid HMAC | Criteria form with job title + org |
| GET /survey | Invalid/expired HMAC | "Link expired" card; "Open localhost:5001 to rate this job" |
| POST /survey | KV write OK | "Feedback saved — syncs to scoring pipeline within the hour" |
| POST /survey | KV write fails | "Something went wrong. Try again or open localhost:5001." |
| Flask POST | SQLite write OK | Redirect to job list + "Feedback saved" banner (existing) |
| Flask POST | SQLite write fails | Redirect to job detail + "Write failed — try again" error banner (bug fix) |

## Out of Scope

- Tailscale / ngrok to expose Flask publicly — not needed with this design
- Real-time push from CF Worker to Flask (impossible without public Flask)
- Pending sync indicator in Flask UI — nice-to-have, not blocking

## Success Criteria

1. Changing a slider label in `config/criteria.yaml` and running
   `python scripts/generate_worker_form.py && wrangler deploy` produces an
   identical form on mobile and desktop.
2. A full criteria survey submitted from a phone shows criteria data in SQLite
   within the hour.
3. "Feedback saved!" only shows when SQLite write actually succeeded.
4. The `/rate` form is removed from new emails; old `/rate` links redirect gracefully.

## Implementation Tasks

Synthesized from design review findings. Run with Claude Code; checkbox as you ship.

- [x] **T1** — `config/criteria.yaml` + `feedback/server.py` load from YAML
- [x] **T2** — `scripts/generate_worker_form.py` — generator reads criteria.yaml, patches index.js
- [x] **T3** — `cloudflare/worker/index.js` — KV removed; CF Worker POSTs directly to Flask API; /survey route with criteria sliders; all 4 interaction states; 32px thumb; job context header
- [x] **T4** — criteria now written directly via Flask `/api/v1/feedback` route (KV path obsolete)
- [x] **T5** — `feedback/server.py` — `_write_feedback_sqlite` returns bool; error banner on failure
- [x] **T6** — `notifier/gmail.py` — "→ Apply" → "→ View job"; "Full feedback (tags + note) →" → "→ Rate in detail"
- [x] **T7** — `notifier/gmail.py` — `_feedback_action_url` for action="survey"; title+org params
- [x] **T8** — `cloudflare/worker/index.js` — /rate serves same survey form (action="rate" HMAC valid)

## Not in Scope

- Tailscale / ngrok to expose Flask publicly — this design makes it unnecessary
- Real-time CF Worker → Flask push — impossible while Flask is localhost
- Pending sync indicator in Flask UI — correct to not build until users feel the pain
- DESIGN.md — recommended but not part of this plan; create after this ships

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 0 | — | — |
| Design Review | `/plan-design-review` | UI/UX gaps | 1 | CLEAN (FULL) | score: 5/10 → 9/10, 3 decisions |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **VERDICT:** Design CLEAR — eng review required before shipping.

## What Already Exists (reuse)

- `criteria` column in SQLite `feedback` table — already added (2026-05-31 migration); no schema change needed
- `feedback/server.py` criteria slider CSS — reference design for mobile form styling
- CF Worker card CSS and `thanksPage()` — already mobile-optimised; reuse both
- `feedback/cf_sync.py` KV polling and SQLite write path — extend, don't replace
