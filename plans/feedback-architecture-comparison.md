# Plan: Feedback Architecture — Three Candidates

Supersedes: `plans/feedback-flask-primary-cf-extension.md` (design review complete)
Closes: [#10](https://github.com/timovanommeren/job_scraper/issues/10)
Branch: main
Status: APPROVED — Architecture C chosen (Cloudflare Tunnel + Flask API)

## Context

The feedback system currently has two parallel write paths that produce different
data shapes, no clear canonical store, and a stale CF Worker form that hasn't
tracked Flask's criteria sliders since commit `e3f8ea6`.

The goal is a **single canonical data store** (SQLite, already exists) that both
Flask (desktop) and CF Worker (mobile email links) converge on, with:
- No diverging data shapes
- A split that makes sense as a standard web architecture pattern
- Independent extensibility of both Flask and CF Worker surfaces
- A clear path for future ML layers that only need to read from one place

Three candidate architectures. All share the same terminal store: local SQLite.

---

## Architecture A: Flask Primary, CF as Generated Satellite

```
┌─────────────────────────────────────────────────────────────┐
│                        EMAIL                                 │
│  Pill tap (1-10) ──► CF /feedback ──► KV                    │
│  Survey link     ──► CF /survey   ──► KV ──► cf_sync ──► SQLite
│                                (hourly)                      │
├─────────────────────────────────────────────────────────────┤
│                      DESKTOP BROWSER                         │
│  Flask /jobs/<id> ──► POST /jobs/<id>/feedback ──► SQLite   │
│                                (immediate)                   │
└─────────────────────────────────────────────────────────────┘

CRITERIA source of truth: config/criteria.yaml
CF Worker form: generated artifact — scripts/generate_worker_form.py
Rule: never edit index.js form HTML directly
```

**Write paths:** 2 (Flask direct, CF → KV → sync)
**Write latency:** Flask: 0ms · CF: 0–60 min
**CRITERIA ownership:** Flask (via YAML), CF mirrors at deploy time
**Extensibility:** Add new form fields → update criteria.yaml, re-run generator, redeploy Worker
**ML data path:** Read from SQLite only. KV is a temporary buffer, not a source.

Pros:
- Flask stays simple (no outbound HTTP, no API layer)
- CF Worker stays simple (no routing logic, just a form)
- Two write paths are clearly explained by latency, not purpose
- Standard "edge satellite + authoritative backend" pattern

Cons:
- Two write paths with different latency — cf_sync.py must never fall behind or phone ratings disappear
- CF Worker form is a generated artifact, not a live mirror — requires a deploy step on every criteria change
- If cf_sync.py is misconfigured or the Task Scheduler job fails, phone ratings are silently lost

---

## Architecture B: CF Worker as Unified Feedback Ingestion API

```
┌─────────────────────────────────────────────────────────────┐
│                        EMAIL                                 │
│  Pill tap (1-10) ──► CF /api/feedback ──► KV ──► cf_sync   │
│  Survey link     ──► CF /survey ──────► KV ──► cf_sync     │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│                      DESKTOP BROWSER                         │
│  Flask /jobs/<id> ──► POST /jobs/<id>/feedback              │
│                         │                                    │
│                    outbound HTTP                             │
│                         │                                    │
│                         ▼                                    │
│              CF /api/feedback ──► KV ──► cf_sync ──► SQLite │
│                                (hourly poll)                 │
└─────────────────────────────────────────────────────────────┘

CRITERIA source of truth: config/criteria.yaml (Flask reads at runtime)
CF Worker: receives all writes, validates HMAC or API key for Flask calls
Flask: reads from SQLite (always up-to-date via sync), writes via CF API
```

**Write paths:** 1 (all feedback routes through CF Worker → KV → sync)
**Write latency:** All sources: 0–60 min (uniform, but delayed)
**CRITERIA ownership:** Flask (runtime), CF form generated at deploy
**Extensibility:** Add new field → update criteria.yaml, update CF API schema, redeploy Worker
**ML data path:** Read from SQLite only. Single aggregation point.

Pros:
- Genuinely single write path — no "which path did this come from?" ambiguity
- All feedback has the same latency and the same data shape regardless of source
- CF Worker becomes a real API rather than a form server — scales to Telegram bot, browser extension, etc.
- Standard "API gateway" pattern — textbook web architecture

Cons:
- Desktop feedback now has 0–60 min lag — the 07:00 scoring run can miss ratings submitted at 06:55
- Flask making outbound HTTP to CF on every submit adds a network dependency; if CF is down, desktop submit fails
- If cf_sync.py falls behind, ALL feedback is delayed (not just phone) — higher blast radius
- Flask is no longer authoritative; it becomes a client of its own satellite

---

## Architecture C: Cloudflare Tunnel — Flask as Single API, CF as Pure Client

```
┌─────────────────────────────────────────────────────────────┐
│                        EMAIL                                 │
│  Pill tap (1-10) ──► CF /feedback (HMAC verify) ──────────► │
│  Survey link     ──► CF /survey (form served)   ──POST────► │
│                                                              │
│                         internet                             │
│                              │                               │
│                     Cloudflare Tunnel                        │
│                              │                               │
│                              ▼                               │
│              Flask /api/v1/feedback ──► SQLite (immediate)  │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│                      DESKTOP BROWSER                         │
│  Flask /jobs/<id> ──► POST /jobs/<id>/feedback ──► SQLite   │
│                                (immediate)                   │
└─────────────────────────────────────────────────────────────┘

CRITERIA source of truth: Flask (runtime)
CF Worker: pure client — HMAC verify + form serve + proxy to Flask API
No KV store for feedback. No cf_sync.py. No polling.
```

**Write paths:** 1 (all feedback proxied through CF Worker to Flask REST API)
**Write latency:** All sources: near-zero (CF Worker proxies synchronously)
**CRITERIA ownership:** criteria.yaml (same generator pattern as Architecture A) — form HTML is baked into index.js at deploy time via `scripts/generate_worker_form.py`. Rule: never edit form HTML directly in index.js.
**Extensibility:** Add new field → update criteria.yaml + re-run generator + wrangler redeploy. A health check in the weekly digest flags schema drift if phone feedback and desktop feedback write different criteria keys to the `feedback.criteria` column.
**ML data path:** Read from SQLite only. Flask is the sole writer.

Pros:
- True single write path with zero lag — phone and desktop feedback both land in SQLite immediately
- Flask is unambiguously authoritative — no two data sources, no sync jobs, no buffers
- CF Worker becomes truly thin: HMAC verify, serve form, forward POST — no business logic
- Kills cf_sync.py entirely — one less moving part, one less Task Scheduler job
- Standard "edge proxy + origin API" pattern — exactly how Cloudflare is designed to be used

Cons:
- Requires a tunnel to expose Flask to the internet (Cloudflare Tunnel, Tailscale, or similar)
- Flask must be running for ANY phone feedback to work — if it crashes or is restarted, mobile writes fail
- Tunnel adds infra to set up, monitor, and maintain
- Security surface: Flask API is now internet-accessible (mitigated by HMAC auth on the API endpoint)
- More complex initial setup than A or B

---

## Comparison Table

| Dimension | A: Flask Primary + Satellite | B: CF as Ingestion API | C: Tunnel + Flask API |
|---|---|---|---|
| Write paths to SQLite | 2 | 1 (via sync) | 1 (direct) |
| Desktop write latency | 0 ms | 0–60 min | ~100 ms |
| Phone write latency | 0–60 min | 0–60 min | ~100 ms |
| CRITERIA source of truth | criteria.yaml (Flask) | criteria.yaml (Flask) | criteria.yaml (generator, baked at deploy) |
| CF Worker role | Form server (generated) | API gateway | Edge proxy + form server (generated) |
| cf_sync.py required | Yes | Yes | No |
| Infra additions | None | None | Cloudflare Tunnel |
| Flask internet exposure | No | No | Yes (via tunnel) |
| Blast radius of cf_sync failure | Phone ratings lost | ALL ratings lost | Zero (no sync) |
| Expandability to new surfaces (bot, extension) | Medium (add surface → CF handles) | High (route to CF API) | High (CF proxies to Flask) |
| Future ML data path | SQLite only | SQLite only | SQLite only |
| Standard pattern name | Edge satellite | API gateway | Edge proxy + origin API |

---

## Key Questions for Eng Review

1. Is the 0–60 min lag on desktop feedback in Architecture B acceptable, given the 07:00 scoring run?
2. Does Architecture C's tunnel setup cost (one-time) outweigh the benefit of eliminating cf_sync.py (forever)?
3. Which architecture makes adding a third feedback surface (Telegram bot, browser extension) the most straightforward?
4. Which architecture is easiest to reason about when something goes wrong at 07:00?

---

## Chosen Architecture: C — Cloudflare Tunnel + Flask as Origin API

**Architecture B eliminated:** desktop feedback would have 0-60 min lag, missing the 07:00 scoring run. Blast radius of cf_sync failure increases. No compensating benefit over A.

**Architecture C chosen over A** because: single write path with zero lag everywhere; kills cf_sync.py entirely (removes Task Scheduler job, KV namespace, polling loop); CF Worker is a genuinely thin edge proxy (HMAC verify + form serve + fetch forward); future surfaces (Telegram, browser extension) just POST to Flask API with no knowledge of KV or polling.

### Final Data Flow

```
EMAIL (from notifier/gmail.py)

  "View job"     ─────────────────────────────────► external job URL
  "Rate in detail" ──► CF /survey ──► mobile form ──► POST /survey
  "Skip suggestion" ─► CF /feedback?skip ──────────► POST

                            │  HMAC verify + fetch()
                            │
                  Cloudflare Tunnel (cloudflared service)
                            │
FLASK (localhost:5001, always-on logon process)

  POST /api/v1/feedback        ──► verify HMAC ──► SQLite + JSON store
  POST /api/v1/skip-suggestion ──► verify HMAC ──► SQLite
  GET  /jobs/<id>                (desktop browser, unchanged)
  POST /jobs/<id>/feedback       (desktop, direct SQLite write, unchanged)
```

### Decisions Made During Eng Review

| # | Decision | Choice |
|---|---|---|
| D1 | Architecture | C (Cloudflare Tunnel) |
| D2 | Pill taps | Removed from emails entirely |
| D3 | Skip-suggestion | Through tunnel → kills cf_sync.py fully |
| D4 | Flask API auth | HMAC in body, reuse CF_WORKER_SECRET |
| D5 | Flask API file | New `feedback/api.py` Blueprint at /api/v1 |
| D6 | Survey form criteria rendering | Baked in at deploy via `scripts/generate_worker_form.py` (same generator pattern as Architecture A). Rule: criteria changes require criteria.yaml update + generator re-run + wrangler redeploy. Health check detects drift. |
| D7 | Tunnel route exposure | Flask read routes (/jobs, /feedback) are publicly accessible via the tunnel. Accepted for a personal pipeline — data is job scores/notes, not credentials. URL is documented in wrangler.toml as intentionally public. |
| D8 | Criteria single source of truth | `config/criteria.yaml` created in T3. `server.py` loads from it at startup (replaces hardcoded CRITERIA constant). Generator reads the same file. True single source of truth. |

### Eliminated

- `feedback/cf_sync.py` — entire file, delete it
- `tests/test_cf_sync.py` — entire file, delete with cf_sync.py
- `FEEDBACK_KV` KV namespace in Cloudflare — no longer needed
- `/poll` and `/delete` routes in CF Worker — dead code, remove
- `/feedback` pill tap route in CF Worker — removed (pills gone from email)
- `JobScraperFeedbackSync` Task Scheduler entry — decommission
- Rating row (1-10 numbered pills) in email `job_html()` — remove from `notifier/gmail.py`

### New Files

| File | Purpose |
|---|---|
| `feedback/api.py` | Flask Blueprint at /api/v1 — POST /feedback and /skip-suggestion |
| `notifier/hmac_utils.py` | Shared `sign_action()` and `verify_action()` — replaces 3 duplicate expressions |
| `config/criteria.yaml` | Single source of truth for feedback criteria dimensions. Created from existing `CRITERIA` constant in server.py. Read by both `generate_worker_form.py` and `feedback/server.py` at startup. |
| `scripts/generate_worker_form.py` | Reads `config/criteria.yaml`, writes survey form HTML (sliders, labels) into `cloudflare/worker/index.js`. Invoked automatically by wrangler.toml `[build]` step before every `wrangler deploy`. Fails loudly (FileNotFoundError) if criteria.yaml missing. |
| `tests/test_feedback_api.py` | 7 test cases for the new Blueprint |
| `tests/test_hmac_utils.py` | Tests for sign_action + verify_action |

### Files Modified

| File | Change |
|---|---|
| `cloudflare/worker/index.js` | Replace /rate with /survey; replace /feedback pill handler with /feedback?skip handler only; add fetch() to tunnel for all writes; remove KV writes; remove /poll /delete |
| `notifier/gmail.py` | Remove rating row from `job_html()`; rename "Full feedback →" to "Rate in detail"; update `_feedback_action_url` for action="survey"; refactor HMAC signing to use `hmac_utils` |
| `feedback/server.py` | Register api Blueprint; fix `_write_feedback_sqlite` silent failure; refactor HMAC signing to use `hmac_utils` |
| `tests/test_gmail_signing.py` | Update: action="rate" → "survey" for full form; assert pill tap URL generation removed |
| `CLAUDE.md` | Update: cf_sync.py removed; api.py added; Task Scheduler entry decommissioned |
| `ARCHITECTURE.md` | Update data flow diagram |

### Infrastructure: Cloudflare Tunnel Setup (one-time)

1. `winget install Cloudflare.cloudflared` (or download from Cloudflare dashboard)
2. `cloudflared tunnel login` → browser OAuth to Cloudflare account
3. `cloudflared tunnel create job-scraper-feedback`
4. Configure `~/.cloudflared/config.yml` — the tunnel must map a hostname on a CF-managed domain to Flask's localhost port:
   ```yaml
   tunnel: <tunnel-id>
   credentials-file: ~/.cloudflared/<tunnel-id>.json
   ingress:
     - hostname: feedback-api.timovanommeren.com
       service: http://localhost:5001                     # NOT /api — Flask Blueprint handles the path prefix
     - service: http_status:404
   ```
   In CF DNS: add CNAME `feedback-api` → `<tunnel-id>.cfargotunnel.com`
5. Register as a Windows Task Scheduler **logon task** (not a Windows service — service approach requires admin and runs as LocalSystem which cannot access user-profile credentials):
   ```powershell
   Register-ScheduledTask -TaskName "CloudflaredTunnel" `
     -Action (New-ScheduledTaskAction -Execute "C:\Program Files (x86)\cloudflared\cloudflared.exe" `
       -Argument '--config "C:\Users\timov\.cloudflared\config.yml" tunnel run') `
     -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME) `
     -Settings (New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
       -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable)
   schtasks /run /tn "CloudflaredTunnel"
   ```
   **DONE** — `CloudflaredTunnel` task is registered and tunnel verified healthy 2026-06-04.
6. In CF Worker `wrangler.toml`: set `FLASK_API_URL = "https://feedback-api.timovanommeren.com"` — the Worker's fetch() calls resolve to `$FLASK_API_URL/api/v1/feedback` which routes through the tunnel to Flask. **DONE** — already in wrangler.toml.

### Test Plan

```
[+] feedback/api.py
  ├── [GAP] POST /api/v1/feedback — valid HMAC → SQLite write → 200 JSON
  ├── [GAP] POST /api/v1/feedback — invalid HMAC → 403
  ├── [GAP] POST /api/v1/feedback — missing job_id → 400
  ├── [GAP] POST /api/v1/feedback — malformed JSON body → 400
  ├── [GAP] POST /api/v1/feedback — SQLite failure → 500 + JSON error
  ├── [GAP] POST /api/v1/skip-suggestion — valid → status=skipped → 200
  ├── [GAP] POST /api/v1/skip-suggestion — invalid HMAC → 403
  └── [GAP] POST /api/v1/skip-suggestion — suggestion_id not found → 404

[+] notifier/hmac_utils.py
  ├── [GAP] sign_action(secret, payload) returns 16-char hex
  ├── [GAP] verify_action(secret, payload, sig) returns True/False
  └── [GAP] verify_action with prior-week bucket → False (7-day expiry works correctly)

[+] scripts/generate_worker_form.py
  ├── [GAP] reads criteria.yaml → HTML contains all slider keys + labels
  └── [GAP] missing criteria.yaml → FileNotFoundError (not silent)

[+] config/criteria.yaml + server.py startup
  └── [GAP] server.py loads CRITERIA from criteria.yaml at startup (not hardcoded)

[+] notifier/gmail.py
  ├── [★★ PARTIAL] HMAC signing — update test_gmail_signing.py for new actions
  └── [GAP] Assert rating row no longer present in job_html() output
```

### Implementation Tasks

- [ ] **T1 (P1, human: ~30min / CC: ~5min)** — `notifier/hmac_utils.py` — Extract sign_action + verify_action; replace 3 duplicate expressions in gmail.py, server.py, test_gmail_signing.py
- [ ] **T5 (P1, human: ~45min / CC: ~8min)** — `feedback/server.py` + `config/criteria.yaml` — ① Fix _write_feedback_sqlite silent failure (raises on DB error instead of swallowing); ② Create config/criteria.yaml from existing CRITERIA constant; ③ Replace hardcoded CRITERIA with `yaml.safe_load("config/criteria.yaml")` at startup; ④ Register api Blueprint; ⑤ Refactor HMAC signing to hmac_utils. **Must run before T2** — Blueprint calls _write_feedback_sqlite.
- [ ] **T2 (P1, human: ~2h / CC: ~15min)** — `feedback/api.py` — New Flask Blueprint with POST /api/v1/feedback and POST /api/v1/skip-suggestion; full HMAC verification; all responses are `jsonify({})` (never plain strings); register in server.py. Depends on T5's helper fix and T1's hmac_utils.
- [ ] **T3 (P1, human: ~3h / CC: ~30min)** — `cloudflare/worker/index.js` + `scripts/generate_worker_form.py` + `cloudflare/worker/wrangler.toml` — ① Write generator script: reads config/criteria.yaml, writes survey form HTML (sliders, job title, 32px thumb) into index.js; fails with FileNotFoundError if criteria.yaml missing; ② wrangler.toml [build] command already added (see wrangler.toml); ③ note: [build] runs `python ../../scripts/generate_worker_form.py` from cloudflare/worker/ — verify path resolution on first deploy; ④ replace /rate with /survey; ⑤ remove /feedback pill handler; ⑥ add fetch() to FLASK_API_URL/api/v1/feedback + /api/v1/skip-suggestion for all writes (HMAC in body); ⑦ remove KV writes + /poll + /delete routes; ⑧ remove [[kv_namespaces]] block from wrangler.toml (enables KV namespace deletion); ⑨ no /rate → /survey redirect (D6: old links 403 accepted, see Migration Caveats)
- [ ] **T4 (P1, human: ~1h / CC: ~8min)** — `notifier/gmail.py` — Remove rating row from job_html(); rename link text; update _feedback_action_url for action="survey" and title+org params; refactor to hmac_utils
- [ ] **T6 (P1, human: ~45min / CC: ~8min)** — `tests/` — Delete test_cf_sync.py; add test_feedback_api.py (7 planned + 1 new: malformed JSON body → 400); add test_hmac_utils.py (sign_action + verify_action + expired-bucket test: prior-week sig → False); update test_gmail_signing.py; add generator test: reads criteria.yaml → correct slider HTML; generator missing-file → FileNotFoundError; server.py loads CRITERIA from criteria.yaml at startup
- [ ] **T7 (P1, human: ~30min / CC: ~5min)** — `feedback/cf_sync.py` — Delete file entirely; remove import from main.py; decommission Task Scheduler entry: `schtasks /delete /tn "JobScraperFeedbackSync" /f`
- [x] **T8 (DONE)** — Infrastructure — cloudflared v2026.5.2 installed; tunnel `job-scraper-feedback` (dac7000c) created; DNS CNAME `feedback-api.timovanommeren.com` → tunnel; `CloudflaredTunnel` Task Scheduler logon task registered; wrangler.toml `FLASK_API_URL` set. Tunnel verified healthy 2026-06-04.
- [ ] **T9 (P1, human: ~15min / CC: ~3min)** — `cloudflare/worker/index.js` — Add AbortSignal.timeout(5000) to all fetch() calls to Flask API. Without this, a slow Flask hangs the CF Worker for 30s — worse UX than the old 60-min polling lag.
- [ ] **T10 (P2, human: ~30min / CC: ~5min)** — `CLAUDE.md` + `ARCHITECTURE.md` — Update for eliminated files, new files, new Task Scheduler state, new data flow
- [ ] **T11 (P2, human: ~1h / CC: ~10min)** — Weekly digest health report — Add a criteria schema drift check: query `feedback` table for all distinct keys present in the `criteria` JSON column, compare against current `config/criteria.yaml` keys. If sets differ (e.g. phone feedback wrote `{"topic_fit":...}` but desktop wrote `{"relevance":...}`), emit a WARNING in the weekly digest health section. This is the safety net for the "both places must match" rule introduced by D6.

### Not in Scope

- Cloudflare KV namespace deletion (manual step in CF dashboard after confirming no remaining entries)
- Telegram bot / browser extension feedback surfaces (these now just need to POST to Flask API when built)
- DESIGN.md creation (recommended, not blocking)
- Tailscale as alternative to Cloudflare Tunnel (not needed; Cloudflare Tunnel integrates with existing CF account)

### Migration Caveats

- **Old survey links will 403 after deploy.** Survey links in emails sent 0–7 days before deployment are signed with `action="rate"` in the HMAC payload. After T4 changes the action to `"survey"`, those links fail CF Worker HMAC verification. No data is lost — new emails work immediately. Impact: at most one week of digests have dead survey links. Decision D1: accepted, not patched.

### What Already Exists (reuse)

- HMAC signing pattern in gmail.py — extract to utility, not rewrite
- Flask `_write_feedback_sqlite` + `_write_feedback_json` in server.py — new Blueprint calls these same helpers
- Flask `skip_suggestion()` GET route — new POST /api/v1/skip-suggestion calls the same SQLite update logic
- CF Worker `thanksPage()` + card CSS — reuse in /survey confirmation page
- `criteria` column in SQLite `feedback` table — already exists, no migration needed

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 2 | CLEAN | run 1: 5 arch decisions; run 2 (tunnel focus): 5 arch decisions (D4–D8), 2 CQ fixes (JSON responses, task ordering), 4 new test gaps, T8 marked done, wrangler.toml [build] + KV cleanup added |
| Design Review | `/plan-design-review` | UI/UX gaps | 2 | CLEAN (FULL) | score: 5/10 → 9/10, 3 decisions; run 2: 5 /review findings implemented, 2 decisions (D1 HMAC caveat, D6 criteria rendering) |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **VERDICT:** ENG + DESIGN CLEARED — ready to implement.
