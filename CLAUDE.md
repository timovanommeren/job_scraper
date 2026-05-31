# CLAUDE.md — Job Scraper Project Briefing

> This file is read automatically by Claude Code on every session start.
> It is NOT documentation for humans — see README.md and ARCHITECTURE.md for that.
> Purpose: give every future Claude Code session enough context to make good decisions
> without asking clarifying questions.

---

## Project Identity

A personal, automated job-hunting pipeline for **Timo van Ommeren** (timovanommeren@gmail.com). It scrapes 24 job sources daily, scores each posting against Timo's profile using the **Anthropic API (Claude Haiku)**, and sends a Gmail digest when strong matches (score ≥ 8/10) are found. A local Flask app at `localhost:5001` lets Timo browse all results and give feedback that actively recalibrates future scoring. Target roles: PhD positions in social/behavioural sciences, paid EU/UN traineeships, research analyst roles at policy think tanks.

Full system design: [ARCHITECTURE.md](ARCHITECTURE.md). Public overview: [README.md](README.md).

---

## Quick Reference

### CLI Commands (all flags that exist in `main.py`)

```bash
python main.py                             # Full pipeline: scrape → score → DB → email
python main.py --test                      # Scrape + score, print digest preview — NO DB writes, NO email
python main.py --dry-run                   # Scrape + score + DB write — NO email, NO preview
python main.py --site <name>               # Run one scraper only (e.g. --site euraxess)
python main.py --site <name> --test        # Test one scraper safely — no side effects
python main.py --weekly-digest             # Send weekly summary of last 7 days and exit
python main.py --weekly-digest --test      # Preview weekly digest without sending
python main.py --backfill-deadlines        # Fill NULL deadlines for existing DB jobs via API
python main.py --reprocess <N>             # Re-score last N rows from failed_extractions table
```

**`--test` vs `--dry-run` are NOT the same:**
- `--test`: no DB writes, but prints a digest preview to stdout. Good for seeing what would be emailed.
- `--dry-run`: no DB writes, no preview, no email. True dry run — silent.
- Both pass `dry_run=True` to `score_new_jobs()`. Only `--test` calls `send_digest(test_mode=True)`.

### Local Interfaces

- Flask job browser: `http://localhost:5001` (auto-starts at Windows logon)
- Health check: `http://localhost:5001/health`

### Most Critical Rules (full list in NEVER Rules section)

- **Never modify any working scraper** (Euraxess, AcademicTransfer, RAND, CASE Poland, JRC, Impactpool, Busara, WODC, SCP, Trimbos, FGV, EPSO Blue Book) unless the task explicitly targets them.
- **Anthropic API only.** Never use Ollama, OpenAI, or any other LLM provider.
- **Never add `anthropic.Anthropic()` calls outside `agents/extractor_scorer.py`.**
- **Never push to GitHub without being asked.** Save locally and present for review.
- **Never re-enable a disabled scraper without reading its GitHub issue first** — they are blocked at infrastructure level (CDN, Cloudflare), not code bugs.

---

## Current System State

### Working Scrapers (do not break these)

| Source | File | Method |
|---|---|---|
| Euraxess | `scrapers/euraxess.py` | requests + BS4, paginated |
| AcademicTransfer | `scrapers/academictransfer.py` | requests + BS4, paginated |
| RAND Corporation | `scrapers/rand.py` | Workday CXS JSON API (POST) |
| CASE Poland | `scrapers/case_poland.py` | requests + BS4 |
| JRC | `scrapers/jrc.py` | requests + BS4 |
| Impactpool | `scrapers/impactpool.py` | requests + BS4 |
| Busara Center | `scrapers/busara.py` | Lever ATS JSON API (GET) |
| WODC | `scrapers/wodc.py` | Bloomreach CMS endpoint, requests |
| SCP | `scrapers/scp.py` | Bloomreach CMS endpoint, requests |
| Trimbos-instituut | `scrapers/trimbos.py` | Playwright |
| FGV | `scrapers/fgv.py` | Playwright (portal.fgv.br rejects Python TLS) |
| EPSO Blue Book | `scrapers/epso_bluebook.py` | requests + BS4 |
| Utrecht University | `scrapers/dutch_universities.py` | requests + BS4, `li.overview-list__item` |
| Tilburg University | `scrapers/dutch_universities.py` | Playwright, SAP SuccessFactors; pre_click "Search Jobs"; `a[href*=career_job_req_id]` |
| Erasmus University Rotterdam | `scrapers/dutch_universities.py` | requests + BS4, `div.teaser` + pagination |
| Radboud University | `scrapers/dutch_universities.py` | requests + BS4, `div.node--type-vacancy` |
| University of Amsterdam | `scrapers/dutch_universities.py` | Playwright, werkenbij.uva.nl |
| Vrije Universiteit Amsterdam | `scrapers/dutch_universities.py` | Playwright, werkenbij.vu.nl |
| University of Groningen | `scrapers/dutch_universities.py` | Playwright, click-navigate (WordPress vacature, no `<a>` tags); returns ~12/run |

### Disabled Scrapers (return `[]` with a WARNING log — do not attempt to fix via code changes)

| Source | File | Reason | Issue |
|---|---|---|---|
| UN Careers | `scrapers/uncareers.py` | CloudFront HTTP 403 — CDN blocks all automation | [#2](https://github.com/timovanommeren/job_scraper/issues/2) |
| OECD | `scrapers/oecd.py` | Cloudflare bot challenge on every request | [#3](https://github.com/timovanommeren/job_scraper/issues/3) |
| BIT | `scrapers/bit.py` | Cloudflare block + confirmed 0 open positions | [#4](https://github.com/timovanommeren/job_scraper/issues/4) |

### Always-Broken Scrapers (runs but always returns 0)

| Source | File | Reason | Issue |
|---|---|---|---|
| TNI | `scrapers/tni.py` | HTTP 429 on every request — IP-level rate limit, not UA-based | [#1](https://github.com/timovanommeren/job_scraper/issues/1) |

### Seasonal Scrapers

| Source | Notes |
|---|---|
| EU Careers | `scrapers/eucareers.py` — EU agency traineeships open only ~March and October. 0 results outside these windows is **expected**. The scraper logs `INFO` (not `WARNING`) when 0 found. |
| EPSO Blue Book | `scrapers/epso_bluebook.py` — European Commission Blue Book traineeship, distinct from EU agency traineeships. Applications open twice yearly (~March and October). When closed, returns 0 with `INFO` log. Session slug is embedded in the URL so March and October sessions get distinct dedup hashes. |

### Known Issues (not yet GitHub issues — flag before fixing)

| Issue | Where | Notes |
|---|---|---|
| Dead function `get_unemailed_jobs()` | `db/dedup.py` | Defined but never called. Weekly digest queries DB directly in `gmail.py`. Safe to leave; don't repurpose without checking callers. |
| Stale model name in `.env.example` | `.env.example` | Shows `claude-haiku-3-5-20251001`; actual default in `extractor_scorer.py` is `claude-haiku-4-5-20251001`. If someone copies `.env.example` verbatim, they get an outdated model name. |
| Weekly digest doesn't deduplicate | `notifier/gmail.py:send_weekly_digest()` | A job emailed on Monday's daily digest will also appear in Tuesday's weekly digest. Intentional (weekly = retrospective), but non-obvious. |
| RUG only returns first-page cards (~12) | `scrapers/dutch_universities.py` | werkenbij.rug.nl uses hash-based pagination. After go_back() the later pages are invisible (hidden DOM nodes). The scraper stops at the first invisible card — only the most recently posted ~12 positions are captured per run. Subsequent pages require navigating through hash pagination which is not currently implemented. |
| Tilburg returns Dutch admin roles too | `scrapers/dutch_universities.py` | SAP SuccessFactors at career5.successfactors.eu lists all 18 Tilburg positions including Dutch-only admin roles. Claude's pre-screen filters most out; only research/academic positions will score high enough to be relevant. |
| Prompt caching inactive on Haiku 4.5 | `agents/extractor_scorer.py` | `extract_and_score` already sends the system prompt in caching-ready list form with `cache_control: {"type": "ephemeral"}` (commit `ef3228b`). Caching is not triggering because `claude-haiku-4-5-20251001` routes through `inference_geo='not_available'`. Will activate automatically when the model gains support. Verify with: `python -c "from anthropic import Anthropic; c=Anthropic(); r=c.messages.create(model='claude-haiku-4-5-20251001',max_tokens=10,system=[{'type':'text','text':'x'*3000,'cache_control':{'type':'ephemeral'}}],messages=[{'role':'user','content':'hi'}]); print(r.usage.cache_creation_input_tokens)"` — non-zero = active. |

---

## Tech Stack

- **Python:** 3.10 (inferred from Task Scheduler path `C:\Python\Python310\python.exe`)
- **LLM:** Anthropic API only. Model: `claude-haiku-4-5-20251001` (default in `extractor_scorer.py`). Override via `CLAUDE_MODEL` env var. **Never use Ollama, never use OpenAI.**
- **LLM client:** `instructor` library wrapping `anthropic.Anthropic()` — validates structured output against Pydantic schema, retries on validation errors.
- **Scraping:** `requests` + `beautifulsoup4` for server-rendered pages; `playwright` (async Chromium, headless) for JS-rendered SPAs.
- **Database:** SQLite 3, WAL mode, path `db/jobs.db` (gitignored). Schema in `db/schema.sql`. 7 tables: `jobs`, `feedback`, `failed_extractions`, `run_log`, `source_suggestions`, `filtered_jobs`, `job_views`. `jobs` has a `content_hash TEXT` column (SHA-256 of normalized title+org for L2 cross-source dedup). `feedback` table has a `tags TEXT` column (JSON array, nullable; legacy — new submissions omit this) and a `criteria TEXT` column (JSON dict of 5 per-dimension scores: `{"topic_fit":3,"methods_fit":5,"org_appeal":4,"career_fit":5,"location_fit":2}`; added 2026-05-31 via `_safe_add_column`). `run_log` has a `source_yields TEXT` column (JSON dict mapping scraper name → jobs fetched per run). `source_suggestions` stores weekly field-intelligence org suggestions (status: `pending` | `skipped`). `filtered_jobs` stores jobs rejected by Layer 2 pre-screen (30-day TTL via `expires_at`; used as negative training examples). `job_views` stores Flask job-detail page views (implicit positive signal for future ranker). `run_log` tracks `jobs_filtered`, `pre_screen_errors`, and `source_yields` (JSON per-source counts). Full schema: [ARCHITECTURE.md](ARCHITECTURE.md#database).
- **Web framework:** Flask 3.x, dev server only, `host="127.0.0.1"` — localhost only, not network-accessible.
- **Email:** Gmail SMTP-SSL (port 465), `smtplib.SMTP_SSL`. Requires Gmail App Password, not account password. Each job card includes a 1–10 rating row; pills link to the CF Worker `/feedback` route with a score param.
- **Scheduling:** Windows Task Scheduler — 4 registered tasks. **Not cron.** See [ARCHITECTURE.md](ARCHITECTURE.md#windows-task-scheduler).
- **Retry logic:** `tenacity` — all scrapers and LLM calls use `@retry` decorators.
- **HTTP retry:** `stop_after_attempt(3)`, `wait_exponential(min=2, max=15)` — standard for scrapers. TNI uses `stop_after_attempt(2)` to limit wasted time.
- **Phone feedback:** Cloudflare Worker (JavaScript, `cloudflare/worker/index.js`) + KV store. Rating row pills and legacy like/pass buttons are HMAC-signed (24-hour daily bucket; action="rate" for pills, action="like"/"pass" for buttons). Skip-suggestion links are also HMAC-signed (action="skip_suggestion"). KV key prefixes: `feedback:{job_id}` for ratings, `skip:{suggestion_id}` for dismissals. `feedback/cf_sync.py` polls the Worker hourly and writes to local DB + `feedback_store.json`. Optional — pipeline works without it.

### Required environment variables (`.env` in project root)

```
ANTHROPIC_API_KEY        # Required. From console.anthropic.com.
CLAUDE_MODEL             # Optional. Default: claude-haiku-4-5-20251001 (not the .env.example value)
GMAIL_ADDRESS            # Required. Gmail account used to send.
GMAIL_APP_PASSWORD       # Required. App Password (Settings → Security → 2FA → App passwords).
NOTIFY_RECIPIENT         # Optional. Defaults to GMAIL_ADDRESS.
RAW_TEXT_MAX_CHARS       # Optional. Default: 4000. Max chars of raw job text sent to Claude.
CF_WORKER_URL            # Optional. Deployed Cloudflare Worker URL for phone feedback.
CF_WORKER_SECRET         # Optional. Shared HMAC secret (set via wrangler secret put CF_WORKER_SECRET).
```

---

## Architecture Summary

**Three processes, never conflate them.** The Flask app (`feedback/server.py`) runs continuously as a persistent Windows logon process — it is always alive, idle except when handling HTTP requests, and never touches the scraper pipeline. The daily scraper (`main.py` via `JobScraperDaily` Task Scheduler at 07:00) and weekly digest (`main.py --weekly-digest` via `JobScraperWeeklyDigest` at Tuesday 08:00) are short-lived — they start, do their work, and exit. All three share one SQLite database (`db/jobs.db`), which uses WAL mode to allow concurrent reads from Flask while `main.py` writes.

**Data flow in one paragraph.** Each of 17 scrapers returns a list of `RawJob` objects (title, url, source, raw_text, org, location, deadline). For each `RawJob`, `db/dedup.py:is_seen()` computes a SHA-256 hash of the canonical URL and checks the `url_hash` index — already seen jobs are skipped (dedup is O(1), happens before any API call). New jobs go to `agents/extractor_scorer.py:extract_and_score()` which calls the Anthropic API with a system prompt loaded from `config/profile.yaml`, augmented with recent feedback examples from `feedback/feedback_store.json` (this is the feedback loop — past feedback with per-criterion scores are injected as few-shot examples via `_item_note()`, and liked organisations from `config/profile.yaml:liked_organizations` receive a score boost). The structured output (`JobPosting` Pydantic model) is inserted into `db/jobs.db`. After all jobs are scored, `notifier/gmail.py:should_send_daily()` checks if any score ≥ 8 — if yes, an HTML digest is emailed. Each job card in the email contains a 1–10 rating row (two rows of 5 HMAC-signed pills); tapping a pill records the score directly via the Cloudflare Worker without opening a form.

**Where to find things for common changes:**
- Change how jobs are scored or what fields are extracted → `agents/extractor_scorer.py` + `config/profile.yaml`
- Change the Layer 2 pre-screen prompt or logic → `agents/extractor_scorer.py:pre_screen()` + `PRESCREEN_SYSTEM_PROMPT`
- Change score thresholds (what gets emailed) → `config/settings.yaml` (`strong_match_threshold`, `email_also_min_score`) — loaded at startup by `notifier/gmail.py:_load_thresholds()`
- Change the email template or subject line → `notifier/gmail.py:build_daily_html()` / `send_weekly_digest()`
- Change the rating row (number pills) in email → `notifier/gmail.py:job_html()` + `_feedback_action_url()`
- Add or modify Flask routes → `feedback/server.py`
- Add a new scraper → `scrapers/` + register in `main.py:build_scraper_registry()`
- Change what gets inserted into the DB → `db/dedup.py:insert_job()` + `db/schema.sql`
- Change how feedback affects scoring → `feedback/profile_updater.py:generate_prompt_additions()`
- Change org boost logic (which orgs get boosted) → `feedback/profile_updater.py:update_liked_organizations()`
- Change what Timo's profile says → `config/profile.yaml` (never overwrite — updated dynamically; `liked_organizations` key is auto-maintained by `update_liked_organizations()`)
- Change the weekly field-intelligence recommender (org suggestions in digest) → `feedback/source_recommender.py`; threshold is `config/settings.yaml:source_recommender.min_jobs`

Full file dependency diagram: [ARCHITECTURE.md — File Dependency Diagram](ARCHITECTURE.md#file-dependency-diagram).

---

## Coding Conventions

### Adding a new scraper

**Step 0 — 5-minute pre-flight before writing any code.** Do this first; it determines the entire approach and prevents writing the wrong scraper type.

```
1. Check robots.txt → crawl-delay or disallowed paths?
2. Recognise the ATS (see table below) — if it matches, the approach is already known.
3. requests.get(list_url) → does the HTML contain job titles?
   Yes → static scraper (requests + BS4). Playwright not needed.
   No  → JS-rendered or requires interaction.
4. GET /sitemap.xml → look for /{type}-sitemap.xml entries (e.g. /vacature-sitemap.xml).
5. DevTools Network tab while page loads → any JSON XHR calls?
   Yes → that endpoint is your API; skip HTML parsing entirely.
   No  → Playwright.
6. If Playwright: does the page show a count ("18 Jobs") but no titles?
   Yes → a button/form interaction is required before results render (e.g. "Search Jobs").
```

**Known ATS platforms — recognise and use the right approach:**

| ATS | How to recognise | Approach |
|---|---|---|
| Lever | URL contains `lever.co` or `jobs.lever.co` | GET `/v0/postings/{company}` JSON API — see `busara.py` |
| Greenhouse | URL contains `greenhouse.io` or `boards.greenhouse.io` | GET `boards-api.greenhouse.io/v1/boards/{company}/jobs` JSON API |
| Workday | URL contains `myworkdayjobs.com` | POST to the CXS search endpoint — see `rand.py` |
| SAP SuccessFactors | URL contains `successfactors.eu/career?company=` | Playwright + `pre_click` to click "Search Jobs"; cards are `a[href*=career_job_req_id]` — see `dutch_universities.py:tilburg` |
| Teamtailor | Subdomain like `careers.{org}.com` with Teamtailor branding | GET `{org}.teamtailor.com/api/v1/jobs` |
| Bloomreach/WerkenbijNL | `werkenvoornederland.nl` component-rendering endpoint | GET with `_hn:type=component-rendering` param — see `wodc.py` |
| WordPress (custom post type) | `wp-json/` in page source; custom post type in URL slug | Check `/wp-json/wp/v2/{post-type}` first; if 404, use click-navigate Playwright — see `dutch_universities.py:rug` |

**Link extraction patterns — identify which applies before writing card selectors:**

| Pattern | How to detect | Solution |
|---|---|---|
| Standard `<a href>` on card | `card.find('a')` returns link | Normal `link_sel` |
| Card IS the `<a>` element | Card selector is `a[href*=...]` directly | `card.get_attribute("href")` works; set `title_sel=""` to use card's own text |
| No `<a>` anywhere on card | No `<a>` found inside or on card element | Click-navigate: `page.expect_navigation` + `card.click()`, capture `page.url`; re-query cards by index after each go_back() — stale handles will fail |
| REST API | JSON XHR in Network tab | Build a requests-based scraper against the API endpoint |

**Common complications to check before testing:**

- **Wrong URL** — always probe 2–3 URL variants before concluding a page doesn't exist. Check the site's own navigation links.
- **Hash-based pagination** (`#page-2`) — breaks after `go_back()` (DOM nodes for later pages remain but become invisible). Use `is_visible()` check; stop iteration when False.
- **Stale Playwright handles** — any element handle stored before a navigation is invalid after it. Always re-query by index (`query_selector_all(sel)[idx]`), never hold handles across navigations.
- **CDN bot blocks** (Cloudflare, CloudFront) — not fixable at the scraping layer. Check if an aggregator already covers the source (e.g. Impactpool for UN jobs). Document as disabled with a GitHub issue.

**These patterns apply equally to conferences and funding calls:**
- Conference management: INDICO has a REST API; ConfTool requires scraping; OpenReview has an API.
- Funding portals: EU Funding & Tenders Portal, NWO, UKRI all have JSON/RSS endpoints — check API docs before scraping HTML.

**Implementation steps (after pre-flight):**

1. Read `scrapers/base.py` — understand `BaseScraper` vs `PlaywrightBaseScraper` and the `RawJob` dataclass.
2. Use `scrapers/euraxess.py` as a template for `requests`-based scrapers; `scrapers/trimbos.py` for Playwright.
3. Subclass `BaseScraper` (or `PlaywrightBaseScraper` for JS-rendered pages). Set `source_name` and `base_url` as class attributes.
4. Implement `fetch(self) -> list` (returns `list[RawJob]`). The method **must** catch all its own exceptions and return `[]` on failure — never raises, never returns `None`.
5. `RawJob` required fields: `title` (str), `url` (str, canonical), `source` (str = `source_name`), `raw_text` (str, ≤4000 chars). Optional: `organization`, `location`, `deadline`.
6. Call `self.canonicalize_url(url)` on every URL — strips tracking params, lowercases, removes trailing slash.
7. Register in `main.py:build_scraper_registry()` — add import and add class to `classes` list.
8. Test in isolation: `python main.py --site <source_name> --test`
9. Add a module-level comment documenting how the site was verified and what selectors were confirmed.

### LLM calls

- All LLM calls go through `agents/extractor_scorer.py`. Do not add `anthropic.Anthropic()` instantiations elsewhere.
- The system prompt is loaded via `_get_full_system_prompt()` — this appends feedback calibration examples. Always use this function; never construct a system prompt inline.
- The response model is `JobPosting` (Pydantic) — `instructor` validates and retries automatically.
- If you need a lightweight one-field extraction (like `_DeadlineOnly`), follow the pattern already in `extractor_scorer.py`.

### Database changes

- SQLite only. All DB access goes through `db/dedup.py` and `db/migrations.py` — no raw SQL in scraper, agent, or notifier files.
- Adding a column: use `db/migrations.py:_safe_add_column()` — it checks `PRAGMA table_info` before `ALTER TABLE`. Never use `DROP TABLE` or `CREATE TABLE ... DROP`.
- `db/dedup.py:get_connection()` must be used for all connections — sets WAL mode and `row_factory = sqlite3.Row`.
- `db/migrations.py:init_db()` is idempotent and safe to call on every startup.

### Flask

- The feedback server is a separate, always-on process. Do not import Flask app state into the scraper pipeline.
- All routes are in `feedback/server.py`. New routes follow the existing pattern (use `_get_db()` for per-request DB connections, `@app.teardown_appcontext` for cleanup).
- The server binds to `127.0.0.1:5001` only — do not change the host to `0.0.0.0`.

### Error handling

- Every scraper must wrap its main extraction logic in a `try/except` that logs the error and returns `[]`. **One malformed job must never abort the whole scrape.**
- Log scraper errors with prefix: `self.logger.exception(f"[{self.source_name}] ...")`.
- Never silently swallow exceptions — log at WARNING or ERROR level. The run_log table records `api_errors` counts for diagnostics.

### Configuration

- User-facing settings → `config/settings.yaml`. Secrets → `.env`. Never hardcode API keys, email credentials, or model names in source files.
- If adding a new threshold or config value, wire it through `settings.yaml` and read it in code. Follow the `_load_thresholds()` pattern in `notifier/gmail.py` as a template.

---

## Layer 2 Pre-filter: B→C Transition

The pipeline runs a cheap Claude Haiku pre-screen (Option B) before full LLM extraction. Option C (sentence-transformer embedding similarity) is the planned long-term replacement. Do NOT migrate until ALL three trigger conditions are met:

1. `Layer 2 mode: B` in the weekly health report AND you are expanding to conferences/funding calls
2. Weekly API cost ≥ €5/month (visible in health report)
3. `Labeled jobs` ≥ 300 (visible in health report)

**Transition checklist (when conditions are met):**
1. Run `scripts/calibrate_threshold.py` — embeds all `db/jobs.db` jobs, finds the natural similarity gap, writes `T_low` and `T_high` to `config/settings.yaml`
2. Replace `pre_screen()` function body in `agents/extractor_scorer.py` with embedding logic (caller interface unchanged: `pre_screen(raw_job, client, content_type='job') -> tuple[bool, str]`)
3. Change `config/settings.yaml: pre_filter.mode` from `"B"` to `"C"`
4. Run `python main.py --test` and confirm filter hit rate is plausible (check weekly health report next Tuesday)

**Key files:**
- `agents/extractor_scorer.py:pre_screen()` — swap function body here only
- `config/settings.yaml:pre_filter.mode` — set to `"C"` after migration
- `TODOS.md` — Option C migration entry has full calibration spec

---

## NEVER Rules

1. **Never modify the scraping logic, selectors, or config of these working scrapers:** `euraxess.py`, `academictransfer.py`, `rand.py`, `case_poland.py`, `jrc.py`, `impactpool.py`, `busara.py`, `wodc.py`, `scp.py`, `trimbos.py`, `fgv.py`, `epso_bluebook.py`, and the 7 university scrapers in `dutch_universities.py` — unless the task explicitly targets them.
2. **Never use Ollama, OpenAI, or any LLM provider other than Anthropic.** All scoring calls go through `agents/extractor_scorer.py` with `instructor.from_anthropic()`.
3. **Never add `anthropic.Anthropic()` instantiations outside `agents/extractor_scorer.py`.**
4. **Never hardcode API keys, email credentials, model names, or score thresholds in source files.** Runtime values come from `.env`; model default is in `extractor_scorer.py` (not settings.yaml).
5. **Never use `DROP TABLE` or recreate existing SQLite tables.** Use `ALTER TABLE` with `_safe_add_column()` for schema changes.
6. **Never re-enable a disabled scraper (uncareers, oecd, bit) without reading its GitHub issue.** These are blocked by external infrastructure, not fixable with code changes alone.
7. **Never change `fetch()`'s return type** (must always return `list[RawJob]`, never `None`, never raises). Changing this breaks `main.py:run_scrapers()`.
8. **Never push commits to GitHub without being explicitly asked.** Always save locally and present changes for review first.
9. **Never hardcode score thresholds in source files.** The live gates come from `settings.yaml` (`strong_match_threshold`, `email_also_min_score`), loaded at startup by `notifier/gmail.py:_load_thresholds()`.
10. **Never overwrite `config/profile.yaml`.** It is updated dynamically based on user feedback. Editing it is fine; replacing it in full is not.
11. **Never complete a task that adds or removes a file, adds or changes a CLI flag, adds or disables a scraper, modifies the SQLite schema, adds a Flask route, changes how a process starts or stops, or adds a dependency to requirements.txt — without first updating CLAUDE.md and ARCHITECTURE.md to reflect the change.** Documentation updates are not optional housekeeping; they are part of the definition of "done."

---

## User Profile Summary

Timo van Ommeren is finishing an MSc in Methodology & Statistics (Utrecht University) after a BSc in Psychology (cum laude, UvA). His professional background is quantitative social science: a ~9-month internship at the EU Drug Agency (EUDA/EMCDDA), junior researcher roles in drug policy and Twitter network analysis, and a thesis on AI-assisted systematic reviews using LLM priors in ASReview. Target roles are PhD student positions in social/behavioural science, paid traineeships at EU/UN institutions, and research/analyst roles at think tanks (RAND, BIT, Busara, TNI, CASE Poland) and Dutch research institutes (SCP, WODC, Trimbos). **Postdoc roles (require completed PhD) are hard disqualifiers and must score ≤ 2/10.**

Full profile with exact scoring rules, penalties, and bonus categories: **`config/profile.yaml`** — the authoritative source. This file changes over time as feedback calibrates it. Do not overwrite it.

---

## Open GitHub Issues

| # | Title | Summary | Labels |
|---|---|---|---|
| [#1](https://github.com/timovanommeren/job_scraper/issues/1) | TNI: 429 on every run — IP-level block | Drupal CMS blocks scraper IP before headers are evaluated. Retry/header changes ineffective. | `bug` `scraper` `needs-investigation` |
| [#2](https://github.com/timovanommeren/job_scraper/issues/2) | UN Careers disabled — CloudFront blocks all automation | AWS CDN returns 403 before any page content loads. Playwright and requests both blocked. | `bug` `scraper` `needs-investigation` |
| [#3](https://github.com/timovanommeren/job_scraper/issues/3) | OECD disabled — Cloudflare bot challenge | Cloudflare challenge fires before content loads regardless of UA. | `bug` `scraper` `needs-investigation` |
| [#4](https://github.com/timovanommeren/job_scraper/issues/4) | BIT disabled — Cloudflare + 0 positions | Cloudflare on main site; likely has Greenhouse ATS endpoint. | `scraper` `low-priority` |
| [#6](https://github.com/timovanommeren/job_scraper/issues/6) | EU Careers: Blue Book traineeship not covered | **Resolved 2026-05-29** — `scrapers/epso_bluebook.py` added; scrapes `traineeships.ec.europa.eu`. | `enhancement` `scraper` |

**Before attempting to fix any scraper in this table: read the full issue on GitHub.** These were disabled for infrastructure reasons that code changes alone cannot resolve. The suggested next steps in each issue are where to start.

---

## Common Workflows

### Add a new scraper

```bash
# 1. Read the base classes
# scrapers/base.py  →  RawJob, BaseScraper, PlaywrightBaseScraper

# 2. Create the scraper file (copy euraxess.py as template for requests-based)
cp scrapers/euraxess.py scrapers/mysite.py

# 3. Implement: set source_name, base_url, override fetch() → list[RawJob]
#    Or for JS-rendered: extend PlaywrightBaseScraper, override _extract_jobs(page)

# 4. Register in main.py
#    Add import and add class to the `classes` list in build_scraper_registry()

# 5. Test in isolation — no DB writes, prints what would be scored
python main.py --site mysite --test
```

### Run the full pipeline safely (no email, no DB writes)

```bash
python main.py --test
```

### Run the full pipeline and write to DB but skip email

```bash
python main.py --dry-run
```

### Send a weekly digest email now (instead of waiting for Tuesday)

```bash
python main.py --weekly-digest
```

### Preview the weekly digest without sending

```bash
python main.py --weekly-digest --test
```

### Backfill missing deadlines

```bash
python main.py --backfill-deadlines
# Runs up to 5 concurrent Claude API calls for jobs WHERE deadline IS NULL
```

### Re-score jobs that failed API extraction

```bash
python main.py --reprocess 20   # retry last 20 rows in failed_extractions table
```

### Check what the last run did

```bash
# In SQLite:
# SELECT * FROM run_log ORDER BY started_at DESC LIMIT 5;
# Or read logs/scraper.log directly
```

### Start/stop the Flask server manually

```bash
# Start (if not already running via Task Scheduler):
pythonw feedback\server.py       # windowless (Windows)
python feedback/server.py        # with console output

# Check if running:
curl http://localhost:5001/health

# Task Scheduler management:
schtasks /query /tn "JobScraperFeedbackServer"
schtasks /run   /tn "JobScraperFeedbackServer"
schtasks /end   /tn "JobScraperFeedbackServer"
```

---

## Documentation Maintenance

**What triggers a doc update:**

- New or deleted file with logic
- New, changed, or removed CLI flag
- New, disabled, or re-enabled scraper
- SQLite schema change (`ALTER TABLE`, new table)
- New Flask route
- Change to how a process starts or stops (Task Scheduler, Flask startup)
- New dependency in `requirements.txt`

**Which files to update:**

- `CLAUDE.md` — update the affected section (current state, tech stack, workflows, NEVER rules, open issues)
- `ARCHITECTURE.md` — update the file map, data flow, CLI flags table, or route list as relevant

**How to do it:** At the end of every task that hits a trigger above, before closing the session, reread the affected sections of both files and edit them to match the current state of the codebase. Do not summarise what you changed — just make the files accurate.

---

## TODOS.md ↔ GitHub Issues (bidirectional)

`TODOS.md` and GitHub Issues are kept in sync — they are two views of the same backlog. Timo may create issues remotely (e.g. from his phone); these should be reflected in `TODOS.md` on the next session.

**TODOS.md → GitHub Issues:** At the end of any session that adds entries to `TODOS.md`, create matching GitHub issues via `gh issue create`. Use the TODOS.md entry as the issue body — issues must be self-contained, not just a title. Requires a token with `public_repo` scope stored via `gh auth login --with-token`.

**GitHub Issues → TODOS.md:** At the start of any session where the user mentions a GitHub issue, or when asked to sync, run `gh issue list --state open` and add any issues missing from `TODOS.md` (expand them with full context, not just the title).

**Format convention:**
- Each TODOS.md entry must reference its GitHub issue number (e.g. `[#12](https://github.com/timovanommeren/job_scraper/issues/12)`)
- When an item is completed, close the GitHub issue (`gh issue close <N>`) and remove the TODOS.md entry

**When to sync:** At session start if the user mentions "issues" or "todos", and always as part of `/document-release` (see below).

### Issue labels — required on every issue

Every GitHub issue must have **all three** of these label groups applied before it is considered complete:

**Priority** (pick one):
- `p1` — Must-do; blocked on conditions being met
- `p2` — Deferred; pick up when capacity allows

**Role** (pick all that apply — at least one required):
- `role:ceo` — Strategic or product decision; needs Timo's input on direction
- `role:designer` — Requires UX or visual design work
- `role:engineer` — Pure engineering implementation

**Topic** (pick all that apply — at least one required):
- `bug` — Something broken
- `enhancement` — New feature or improvement
- `architecture` — System design, data flow, or structural concerns
- `ux` — User experience or interface
- `scraper` — Data-collection scraper
- `pre-filter` — Layer 2 pre-screen / filtering pipeline
- `feedback` — Feedback capture, sync, or UI
- `scoring` — Claude scoring, calibration, or prompt tuning
- `tech-debt` — Code correctness or maintainability debt
- `documentation` — Docs only
- `needs-investigation` — Needs research before a fix can be written

### `/document-release` issue audit

As part of every `/document-release` run, check for unlabeled or under-documented issues:

```bash
gh issue list --state open --json number,title,labels,body \
  | python -c "
import sys, json
issues = json.load(sys.stdin)
label_names = lambda i: [l['name'] for l in i['labels']]
for i in issues:
    labels = label_names(i)
    missing = []
    if not any(l in labels for l in ['p1','p2']): missing.append('priority')
    if not any(l.startswith('role:') for l in labels): missing.append('role')
    if not any(l in labels for l in ['bug','enhancement','architecture','ux','scraper',
       'pre-filter','feedback','scoring','tech-debt','documentation','needs-investigation']):
        missing.append('topic')
    if missing or len(i.get('body','') or '') < 100:
        print(f'#{i[\"number\"]} {i[\"title\"]} — missing: {missing or \"body too short\"}')"
```

For each flagged issue: add missing labels, and if the body is too short (< 100 chars), expand it with context from TODOS.md or the session that created it.

---

## gstack

Use the `/browse` skill from gstack for all web browsing. Never use `mcp__claude-in-chrome__*` tools.

Available gstack skills:
- `/office-hours`
- `/plan-ceo-review`
- `/plan-eng-review`
- `/plan-design-review`
- `/design-consultation`
- `/design-shotgun`
- `/design-html`
- `/review`
- `/ship`
- `/land-and-deploy`
- `/canary`
- `/benchmark`
- `/browse`
- `/connect-chrome`
- `/qa`
- `/qa-only`
- `/design-review`
- `/setup-browser-cookies`
- `/setup-deploy`
- `/setup-gbrain`
- `/retro`
- `/investigate`
- `/document-release`
- `/document-generate`
- `/codex`
- `/cso`
- `/autoplan`
- `/plan-devex-review`
- `/devex-review`
- `/careful`
- `/freeze`
- `/guard`
- `/unfreeze`
- `/gstack-upgrade`
- `/learn`

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:
- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore
- Author a backlog-ready spec/issue → invoke /spec
