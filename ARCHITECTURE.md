# Architecture — Job Scraper

> Developer reference. Open this file when you need to understand how something works,
> where to find it, or why a design decision was made.
> For installation, see [SETUP.md](SETUP.md). For a high-level overview, see [README.md](README.md).

---

## Table of Contents

1. [System Topology](#system-topology)
2. [File Dependency Diagram](#file-dependency-diagram)
3. [Data Flow: Scrape → Email](#data-flow-scrape--email)
4. [main.py — Entry Point](#mainpy--entry-point)
5. [CLI Flags Reference](#cli-flags-reference)
6. [Windows Task Scheduler](#windows-task-scheduler)
7. [Scrapers](#scrapers)
8. [Scoring Agent](#scoring-agent)
9. [Database](#database)
10. [Flask Application (feedback/server.py)](#flask-application-feedbackserverpy)
11. [Notifier (gmail.py)](#notifier-gmailpy)
12. [The Feedback Loop](#the-feedback-loop)
13. [Directory Tree & File Roles](#directory-tree--file-roles)
14. [Configuration Reference](#configuration-reference)
15. [Known Limitations & Disabled Scrapers](#known-limitations--disabled-scrapers)

---

## System Topology

Three distinct processes exist in this system. They run independently and interact only through the shared SQLite database and the JSON feedback store.

```
┌──────────────────────────────────────────────────────────────────┐
│ PROCESS 1 — main.py (triggered daily by Task Scheduler)          │
│   Scrape → Deduplicate → Score → Insert DB → Send email          │
│   Runs at: 07:00 daily. Duration: ~2–5 minutes.                  │
│   Idle between runs: does not exist as a process.                │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│ PROCESS 2 — feedback/server.py (persistent, always-on)           │
│   Flask app on http://localhost:5001                              │
│   Idle behaviour: waits for HTTP requests; touches nothing.      │
│   Starts: at Windows logon via Task Scheduler logon trigger.     │
│   When idle: 0% CPU, ~35–50 MB RAM.                              │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│ PROCESS 3 — main.py --weekly-digest (triggered weekly)           │
│   Queries DB for last 7 days → sends weekly summary email        │
│   Runs at: 08:00 every Tuesday.                                  │
│   Runs independently of daily scrape results.                    │
└──────────────────────────────────────────────────────────────────┘
```

**What the Flask app does when no one is using it:** Nothing. Flask's development server blocks on `app.run()`. It is idle, consuming only a small amount of RAM. It does not poll the database, run background threads, or modify any files. It wakes up only when an HTTP request arrives.

**What triggers the daily scrape:** Windows Task Scheduler fires `JobScraperDaily` at 07:00, which runs `run.bat`, which runs `python main.py`.

**What triggers the weekly digest:** Windows Task Scheduler fires `JobScraperWeeklyDigest` at 08:00 every Tuesday, which runs `python main.py --weekly-digest`.

---

## File Dependency Diagram

```
run.bat  (Task Scheduler → daily at 07:00)
  └── main.py
        ├── config/settings.yaml          (runtime config)
        ├── scrapers/euraxess.py
        ├── scrapers/impactpool.py
        ├── scrapers/uncareers.py          [DISABLED]
        ├── scrapers/eucareers.py
        ├── scrapers/oecd.py               [DISABLED]
        ├── scrapers/academictransfer.py
        ├── scrapers/jrc.py
        ├── scrapers/rand.py
        ├── scrapers/tni.py                [always 429]
        ├── scrapers/case_poland.py
        ├── scrapers/busara.py
        ├── scrapers/wodc.py
        ├── scrapers/scp.py
        ├── scrapers/trimbos.py
        ├── scrapers/bit.py                [DISABLED]
        ├── scrapers/fgv.py
        ├── scrapers/epso_bluebook.py
        │     └── (all scrapers import scrapers/base.py: RawJob, BaseScraper)
        │
        ├── agents/extractor_scorer.py  →  Anthropic API (claude-haiku-4-5-*)
        │     ├── config/profile.yaml      (system prompt — authoritative)
        │     └── feedback/profile_updater.py
        │           └── feedback/store.py  →  feedback/feedback_store.json
        │
        ├── db/migrations.py  →  db/schema.sql  →  db/jobs.db
        ├── db/dedup.py       →  db/jobs.db
        └── notifier/gmail.py
              └── feedback/profile_updater.py  (email footer)

feedback/server.py  (Task Scheduler → at logon, always-on)
  ├── db/dedup.py       →  db/jobs.db        (job list, feedback table)
  ├── feedback/store.py →  feedback/feedback_store.json
  └── feedback/profile_updater.py            (email footer HTML)
```


---

## Data Flow: Scrape → Email

```
  Task Scheduler (07:00)
         │
         ▼
      run.bat
         │
         ▼
     main.py  ─── load config/settings.yaml
         │
         ▼
  ┌─ run_scrapers() ───────────────────────────────────────────┐
  │  for each scraper in registry (17 scrapers):               │
  │    scraper.fetch() → list[RawJob]                          │
  │    RawJob fields: title, url, source, raw_text,            │
  │                   organization, location, deadline         │
  └────────────────────────────────────────────────────────────┘
         │ list[RawJob]  (~40–200 items typically)
         ▼
  ┌─ score_new_jobs() ─────────────────────────────────────────┐
  │  for each RawJob:                                          │
  │    1. db/dedup.py:is_seen(url, conn)                       │
  │       → YES: update last_seen_at, skip                     │
  │       → NO: proceed                                        │
  │    2. agents/extractor_scorer.py:safe_extract_and_score()  │
  │       → POST to Anthropic API (claude-haiku-4-5-*)         │
  │       → returns JobPosting (structured Pydantic model)     │
  │       → fields: title, organization, location,             │
  │                 contract_type, deadline, description_snippet│
  │                 tags[], relevance_score (1–10),             │
  │                 relevance_tier, relevance_reason            │
  │    3. db/dedup.py:insert_job(raw, posting, conn)           │
  │       → writes to jobs table in db/jobs.db                 │
  └────────────────────────────────────────────────────────────┘
         │ list[(RawJob, JobPosting)]  (only new, scored jobs)
         ▼
  ┌─ send_digest() ────────────────────────────────────────────┐
  │  gmail.py:should_send_daily()                              │
  │    → sends email only if ANY score >= 8                    │
  │  if send:                                                  │
  │    strong_rows = jobs with score >= 8                      │
  │    also_rows   = jobs with score 6–7                       │
  │    jobs with score <= 5: stored in DB, not emailed         │
  │    notifier/gmail.py:send_digest()                         │
  │      → SMTP via smtp.gmail.com:465 (SSL)                   │
  │      → email includes: job cards, deadline badges,         │
  │                        feedback buttons, run stats         │
  │    db/dedup.py:mark_emailed(job_ids)                       │
  └────────────────────────────────────────────────────────────┘
         │
         ▼
  db/dedup.py:log_run_finish()  →  run_log table
```

**Where deduplication happens:** In `score_new_jobs()`, before any API call. The function calls `db/dedup.py:is_seen(url, conn)` which computes `SHA-256(canonical_url)` and queries the `url_hash` index. If the hash exists, the job is skipped and `last_seen_at` is updated. This is O(1) and happens before any network call to Anthropic.

**Where scoring happens:** `agents/extractor_scorer.py:extract_and_score()`, called via `safe_extract_and_score()`. The model is `claude-haiku-4-5-20251001` (controlled by `CLAUDE_MODEL` env var). The system prompt comes from `config/profile.yaml` (authoritative) or the embedded fallback in `extractor_scorer.py` if profile.yaml is missing.

**Where the result is stored:** `db/dedup.py:insert_job()` writes to `db/jobs.db` (SQLite), table `jobs`. The full raw_text is preserved (up to 8,000 chars) for potential reprocessing.

---

## main.py — Entry Point

`main.py` is the single orchestrator for all non-server operations. When run with no arguments:

1. `load_settings()` — reads `config/settings.yaml` into a dict
2. `setup_logging()` — configures `RotatingFileHandler` → `logs/scraper.log` + `StreamHandler` → stdout
3. `db.migrations.init_db()` — runs `schema.sql` idempotently; adds `deadline` column if missing
4. `db.dedup.get_connection()` — opens SQLite connection with WAL mode and row_factory
5. `agents.extractor_scorer.build_client()` — creates `instructor.from_anthropic(Anthropic(api_key=...))`
6. `log_run_start(conn)` — inserts a row into `run_log` table, returns `run_id`
7. `run_scrapers(settings)` — calls all 17 registered scrapers, aggregates `list[RawJob]`
8. `score_new_jobs(raw_jobs, conn, client)` — dedup + score + insert (see Data Flow above)
9. `send_digest(new_postings, stats, conn)` — conditionally sends email
10. `log_run_finish(...)` — updates `run_log` row with final stats and status

---

## CLI Flags Reference

All flags are defined in `main.py`'s `argparse` block. Call `python main.py --help` for the authoritative list.

| Flag | What it does | What it skips | When to use |
|---|---|---|---|
| *(none)* | Full pipeline: scrape → score → DB write → email | Nothing | Normal daily run |
| `--test` | Scrape + score, print digest preview to stdout | DB writes, email send | Safe debugging: see what would be emailed without side effects |
| `--dry-run` | Scrape + score + DB write | Email send | Populate DB without triggering notifications |
| `--site <name>` | Run only the named scraper (e.g. `--site euraxess`) | All other scrapers | Debugging a specific scraper; also usable with `--test` |
| `--reprocess <N>` | Re-score the last N rows in `failed_extractions` | Scraping entirely | Recover from temporary API outages |
| `--weekly-digest` | Query last 7 days from DB, send weekly email | Scraping, scoring | Generate weekly summary (also run by Task Scheduler Tuesday 08:00) |
| `--backfill-deadlines` | Run deadline-only extraction for all jobs WHERE deadline IS NULL | Everything else | Retroactively fill in deadline dates for old jobs |

**Combining flags:**
- `--test --weekly-digest` — prints weekly digest preview to stdout, no email
- `--test --site euraxess` — scrapes and scores only Euraxess, prints what would be emailed

**Important subtleties:**
- `--test` and `--dry-run` both skip DB writes during scoring (`score_new_jobs(dry_run=True)`). The difference is that `--test` still calls `send_digest(test_mode=True)` which prints a preview, while `--dry-run` skips `send_digest` entirely.
- `--reprocess` exits immediately after reprocessing; it does not scrape.
- `--backfill-deadlines` uses up to 5 concurrent Anthropic API calls (`ThreadPoolExecutor(max_workers=5)`).

---

## Windows Task Scheduler

Three tasks are registered. All are under the current user account. If the machine is asleep when a task fires, the task runs at next wakeup (Windows default: "Run as soon as possible after a scheduled start is missed").

| Task Name | Command | Schedule | Notes |
|---|---|---|---|
| `JobScraperFeedbackServer` | `pythonw.exe feedback\server.py` | At every user logon | RestartCount=5, interval=2 min. No window. Logs to `logs\server.log`. |
| `JobScraperDaily` | `run.bat` | Daily at 07:00 | `run.bat` checks port 5001 first, starts server if needed, then `python main.py`. Logs to `logs\task_scheduler_output.log`. |
| `JobScraperWeeklyDigest` | `python.exe main.py --weekly-digest` | Every Tuesday at 08:00 | Runs independently of daily scrape. Queries last 7 days from DB. |

**`run.bat` logic:**
```batch
netstat -ano | findstr "127.0.0.1:5001" >nul 2>&1
if errorlevel 1 (
    start /min pythonw.exe feedback\server.py   # start server if port is free
) else (
    echo already running
)
python main.py >> logs\task_scheduler_output.log 2>&1
```

The feedback server is started by `run.bat` as a fallback, but normally it is already running because the `JobScraperFeedbackServer` logon task started it at login. The `run.bat` check prevents a second instance from starting.

**`feedback/server.py` port collision guard:**
```python
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    if s.connect_ex(("127.0.0.1", 5001)) == 0:
        sys.exit(0)   # port in use → silent exit
```

**Verify tasks are registered:**
```bat
schtasks /query /tn "JobScraperFeedbackServer"
schtasks /query /tn "JobScraperDaily"
schtasks /query /tn "JobScraperWeeklyDigest"
```

---

## Scrapers

### Architecture

All scrapers live in `scrapers/` and must extend either `BaseScraper` or `PlaywrightBaseScraper` (both in `scrapers/base.py`).

**`BaseScraper`** (for server-rendered pages or JSON APIs):
- Subclass must implement `fetch(self) -> list[RawJob]`
- `fetch()` must catch all its own exceptions and return `[]` on failure — never raises
- Provides `canonicalize_url(url)`: strips tracking params, lowercases scheme/host, removes trailing slash

**`PlaywrightBaseScraper`** (for JS-rendered pages):
- Subclass must implement `async _extract_jobs(self, page) -> list[RawJob]`
- Base class manages the browser lifecycle: `asyncio.run(_async_fetch())` → launches headless Chromium → navigates → calls `_extract_jobs` → closes browser
- Uses `wait_until="domcontentloaded"` with 15 s timeout (NOT `networkidle`, which stalls on SPAs)
- After `goto()` resolves, individual scrapers call `page.wait_for_selector(...)` with their own timeout

**`RawJob` dataclass** (the output contract):
```python
@dataclass
class RawJob:
    title:        str
    url:          str             # canonical; primary dedup key
    source:       str             # scraper name, e.g. "euraxess"
    raw_text:     str             # full text, HTML-stripped, up to 4000 chars
    organization: Optional[str]
    location:     Optional[str]
    deadline:     Optional[str]   # raw string as found on page
```

### Registered Scrapers (17 total in main.py)

| Source | Class | Method | Status |
|---|---|---|---|
| `euraxess` | `EuraxessScraper` | requests + BeautifulSoup, paginated | ✅ Active |
| `impactpool` | `ImpactpoolScraper` | requests + BeautifulSoup | ✅ Active |
| `uncareers` | `UnCareersScraper` | — | ❌ Disabled ([#2](https://github.com/timovanommeren/job_scraper/issues/2)) |
| `eucareers` | `EuCareersScraper` | Playwright, `a[href*="/trainee/"]` | ✅ Active (seasonal) |
| `oecd` | `OecdScraper` | — | ❌ Disabled ([#3](https://github.com/timovanommeren/job_scraper/issues/3)) |
| `academictransfer` | `AcademicTransferScraper` | requests + BeautifulSoup, paginated | ✅ Active |
| `jrc` | `JRCScraper` | requests + BeautifulSoup | ✅ Active |
| `rand` | `RandScraper` | Workday CXS JSON API (POST) | ✅ Active |
| `tni` | `TNIScraper` | requests + BeautifulSoup | ⚠️ Always 429 ([#1](https://github.com/timovanommeren/job_scraper/issues/1)) |
| `case_poland` | `CasePolandScraper` | requests + BeautifulSoup | ✅ Active |
| `busara` | `BusaraScraper` | Lever ATS JSON API (GET) | ✅ Active |
| `wodc` | `WODCScraper` | Bloomreach CMS endpoint, requests | ✅ Active |
| `scp` | `SCPScraper` | Bloomreach CMS endpoint, requests | ✅ Active |
| `trimbos` | `TrimbosScraper` | Playwright, `a[href*="vacaturebeschrijving"]` | ✅ Active |
| `bit` | `BITScraper` | — | ❌ Disabled ([#4](https://github.com/timovanommeren/job_scraper/issues/4)) |
| `fgv` | `FGVScraper` | Playwright, `a[href^="/vaga/"]` — requests TLS rejected by portal.fgv.br | ✅ Active |
| `epso_bluebook` | `EPSOBluebookScraper` | requests + BeautifulSoup, `section.ecl-banner--no-media` status banner | ✅ Active (seasonal) |


### Retry policy
Most scrapers use `tenacity.retry` with `stop_after_attempt(3)` and `wait_exponential(min=2, max=15)`. Exceptions: TNI uses `stop_after_attempt(2)` to limit wasted time when blocked.

### WODC and SCP — Bloomreach CMS endpoint
Both scrapers use the same undocumented component-rendering endpoint on `werkenvoornederland.nl`:
```
GET /vacatures?_hn:type=component-rendering&_hn:ref=r48_r1_r4&term=<search>
```
If `r48_r1_r4` ever returns empty unexpectedly, open the page in browser DevTools → find `<div id="vacancy-results-container" data-resource="...">` → copy the `_hn:ref` value.

---

## Scoring Agent

**File:** `agents/extractor_scorer.py`

**Model:** `claude-haiku-4-5-20251001` (default; override with `CLAUDE_MODEL` env var)

**Library:** `instructor` (`instructor.from_anthropic(Anthropic(...))`) — validates API output against the `JobPosting` Pydantic schema, retrying on validation errors automatically.

### JobPosting schema (the structured output)

```python
class JobPosting(BaseModel):
    title:               str
    organization:        str
    location:            str
    contract_type:       Optional[str]   # PhD position | postdoc | traineeship | ...
    deadline:            Optional[str]   # YYYY-MM-DD or null
    description_snippet: str             # first 250 chars
    tags:                list             # 2–5 terms from controlled vocabulary
    relevance_score:     int             # 1–10
    relevance_tier:      str             # strong_match (>=8) | maybe (5–7) | not_relevant (<=4)
    relevance_reason:    str             # 1–2 sentences
```

### System prompt

The scoring system prompt is loaded from `config/profile.yaml` (`system_prompt:` key). If the file is missing or unreadable, the code falls back to the embedded `_FALLBACK_SYSTEM_PROMPT` string in `extractor_scorer.py`. The `profile.yaml` version is **more detailed** — it includes hard disqualifiers (postdoc = max score 2), strong penalties (senior roles, climate focus), and bonus categories. Always edit `profile.yaml`, not the embedded fallback.

**System prompt augmentation:** Before every API call, `_get_full_system_prompt()` is called. This appends the output of `feedback/profile_updater.py:generate_prompt_additions()` — a block listing recent liked/passed jobs as few-shot calibration examples. This means **past feedback actively modifies future scoring** in real time.

### Retry policy
`@retry(stop_after_attempt(3), wait_exponential(min=5, max=60))` on `RateLimitError` and `APIConnectionError` only. Other errors are not retried.

### Deadline backfill
`backfill_deadlines(conn, client)` (triggered by `--backfill-deadlines`) runs a focused extraction on jobs where `deadline IS NULL`. Uses a lighter `_DeadlineOnly` model (one field) and `max_workers=5` for parallel calls.

---

## Database

**File:** `db/jobs.db` (SQLite, WAL mode, gitignored)
**Schema defined in:** `db/schema.sql`
**Initialisation:** `db/migrations.py:init_db()` — idempotent, safe to call every startup

### Tables

**`jobs`** — every processed job posting
```
id                  INTEGER PK AUTOINCREMENT
url                 TEXT UNIQUE              -- canonical URL
url_hash            TEXT UNIQUE              -- SHA-256(url), indexed, used for dedup
source              TEXT                     -- scraper name
title               TEXT
organization        TEXT
location            TEXT
contract_type       TEXT
deadline            TEXT                     -- YYYY-MM-DD, nullable
description_snippet TEXT                     -- 250 chars from Claude
tags                TEXT                     -- JSON array: '["statistics","R"]'
relevance_score     INTEGER                  -- 1–10
relevance_tier      TEXT                     -- strong_match | maybe | not_relevant
relevance_reason    TEXT
raw_text            TEXT                     -- preserved up to 8000 chars
first_seen_at       TEXT                     -- ISO 8601 UTC
last_seen_at        TEXT                     -- updated on re-scrape
emailed_at          TEXT                     -- NULL = not yet emailed
is_active           INTEGER DEFAULT 1        -- 0 = no longer found on site
```

**`feedback`** — structured user feedback submitted via Flask form
```
id               INTEGER PK
job_id           TEXT                    -- foreign key to jobs.id (as string)
relevance_score  INTEGER (1–10)          -- user's rating (not Claude's)
mismatch_reasons TEXT                    -- JSON array of reason keys
comment          TEXT
timestamp        DATETIME
```

**`failed_extractions`** — jobs where Claude API call failed
```
id          INTEGER PK
url         TEXT
source      TEXT
raw_text    TEXT
error_msg   TEXT
created_at  TEXT
retried     INTEGER DEFAULT 0
```

**`run_log`** — one row per `main.py` run
```
id              INTEGER PK
started_at      TEXT
finished_at     TEXT
sites_scraped   INTEGER
new_jobs_found  INTEGER    -- truly new URLs (not previously seen in DB)
jobs_scored     INTEGER    -- successfully scored subset of new_jobs_found
jobs_emailed    INTEGER
api_errors      INTEGER
status          TEXT       -- success | partial | failed
```

### Indexes
```sql
idx_jobs_url_hash     ON jobs(url_hash)      -- O(1) dedup check
idx_jobs_source       ON jobs(source)
idx_jobs_emailed_at   ON jobs(emailed_at)
idx_jobs_tier         ON jobs(relevance_tier)
idx_feedback_job_id   ON feedback(job_id)
idx_feedback_ts       ON feedback(timestamp)
```

### Connection settings
```python
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row   # access columns by name
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA foreign_keys=ON")
```

WAL mode allows the Flask server to read the database while `main.py` is writing — no lock contention.

---

## Flask Application (feedback/server.py)

### Role
Provides a local web UI for browsing all scraped jobs, filtering by relevance tier, and submitting structured feedback. It is **not** a public API — it binds to `127.0.0.1:5001` only and is not accessible from other machines on the network.

### How it starts
The `JobScraperFeedbackServer` Windows Task Scheduler task runs `pythonw.exe feedback\server.py` at every user logon. `pythonw.exe` creates no console window and keeps the process alive at login. The server writes all logs to `logs/server.log` via `RotatingFileHandler` (2 MB, 2 backups).

### Flask Routes

| Method | Route | Description |
|---|---|---|
| `GET` | `/` | Redirect to `/jobs` |
| `GET` | `/jobs` | Paginated job list; accepts `?tier=strong_match\|maybe\|not_relevant\|all` and `?page=N` |
| `GET` | `/jobs/<id>` | Job detail page with score/tags/snippet + feedback form (slider + checkboxes) |
| `POST` | `/jobs/<id>/feedback` | Submit feedback; writes to both SQLite `feedback` table and `feedback_store.json` |
| `GET` | `/feedback` | Paginated history of all submitted feedback |
| `GET` | `/fb` | Email button compat: `?id=N` → redirect to `/jobs/N` |
| `GET` | `/comment_form` | Legacy compat: redirects to `/jobs/<id>` |
| `POST` | `/comment` | Legacy compat: writes JSON feedback, redirects |
| `GET` | `/health` | Returns `"OK", 200` — used to test if server is running |

### Tier filter implementation
The `/jobs` filter uses **raw score** (not tier label string), which keeps the filter correct after any scoring boundary changes:
```python
if tier == "strong_match":
    where = "WHERE relevance_score >= 8"
elif tier == "maybe":
    where = "WHERE relevance_score >= 5 AND relevance_score < 8"
elif tier == "not_relevant":
    where = "WHERE relevance_score <= 4"
```

### Database access
Flask uses `flask.g` to hold one SQLite connection per request:
```python
def _get_db():
    if "db" not in g:
        g.db = get_connection()   # WAL mode
    return g.db

@app.teardown_appcontext
def _close_db(exc):
    g.pop("db", None).close()
```

---

## Notifier (gmail.py)

**File:** `notifier/gmail.py`

### Score thresholds (loaded from `config/settings.yaml`)
```python
STRONG_THRESHOLD, ALSO_THRESHOLD = _load_thresholds()
# Reads config/settings.yaml:
#   filtering.strong_match_threshold  → default 8
#   filtering.email_also_min_score    → default 6
# Falls back to (8, 6) if settings.yaml is missing or unreadable.
WEEKLY_LOW_MIN = 1   # weekly digest includes all scored jobs (hardcoded)
```

To change thresholds: edit `config/settings.yaml` — no code change needed. Changes take effect on the next `main.py` run (thresholds are loaded at module import time).

Jobs with score < `email_also_min_score` (< 6 by default) are stored in the DB but **never appear in any email** unless you open `http://localhost:5001/jobs`.

### Daily email logic
1. `should_send_daily(new_postings)` — returns True only if at least one score >= 8
2. If False: logs "No strong matches today. Skipping." and exits — no email sent
3. If True: separates jobs into `strong_rows` (>=8) and `also_rows` (6–7), calls `_smtp_send()`
4. After successful send: `db/dedup.py:mark_emailed(job_ids)` sets `emailed_at` on all sent rows

### Weekly digest logic
Queries `SELECT * FROM jobs WHERE first_seen_at >= ?` (last 7 days). Does **not** filter by `emailed_at` — a job that appeared in the daily email this week will also appear in the Tuesday weekly digest. This is intentional: the weekly provides a complete retrospective.

### SMTP
```python
smtplib.SMTP_SSL("smtp.gmail.com", 465)
server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
```
Credentials from env vars: `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`. Recipient defaults to `GMAIL_ADDRESS`; override with `NOTIFY_RECIPIENT`.

### Fallback on SMTP failure
If `_smtp_send()` raises, `save_fallback_html()` writes the full digest HTML to `logs/digest_fallback_<date>.html` so the content is not lost.

---

## The Feedback Loop

### How feedback is submitted
1. User receives email → clicks "✅ Interested" or "❌ Pass" button
2. Button URL: `http://localhost:5001/fb?id=<job_id>&a=like` → redirects to `/jobs/<id>`
3. Or user goes directly to `http://localhost:5001/jobs/<id>` → full feedback form

### What the form captures
- **Relevance slider** (1–10): user's own score, independent of Claude's score
- **Mismatch reasons** (checkboxes): wrong topic, wrong location, too senior, etc.
- **Free-text comment**: notes or example jobs

### Where feedback is stored (two parallel stores)

| Store | File | Written by | Read by | Purpose |
|---|---|---|---|---|
| SQLite `feedback` table | `db/jobs.db` | `server.py:_write_feedback_sqlite()` | Flask UI (`/feedback` page) | Authoritative history; includes numeric score + reasons |
| JSON flat file | `feedback/feedback_store.json` | `server.py:_write_feedback_json()` via `feedback/store.py` | `feedback/profile_updater.py` | Prompt calibration only; simpler schema |

Both are written on every feedback submission. They are separate and can theoretically diverge (e.g. if the JSON file is deleted but the DB is not).

### Does feedback affect scoring?

**Yes.** The mechanism:

1. Every API call goes through `_get_full_system_prompt()` in `extractor_scorer.py`
2. This calls `feedback/profile_updater.py:generate_prompt_additions()`
3. Which reads `feedback/feedback_store.json` via `feedback/store.py:get_feedback_summary()`
4. Returns a block of text like:
   ```
   ══════════════════════════════
   USER FEEDBACK (calibrate scores based on these past reactions):
   ══════════════════════════════
   JOBS USER LIKED — scored too low; boost similar roles:
     ✅ [9/10] Research Analyst @ WODC
   JOBS USER PASSED ON — scored too high; reduce similar roles:
     ❌ [3/10] Senior Manager @ Climate NGO ← "too senior, wrong field"
   ```
5. This text is appended to the base system prompt before every Claude API call

So feedback given today affects tomorrow's run's scores. The more feedback you give, the better-calibrated the scoring becomes.

---

## Directory Tree & File Roles

```
job_scraper/
├── .env                          # Runtime secrets (gitignored) — API keys, Gmail credentials
├── .env.example                  # Template showing all required env vars
├── .gitignore                    # Excludes .env, logs/, db/*.db, __pycache__/
├── ARCHITECTURE.md               # This file
├── README.md                     # Public-facing overview
├── SETUP.md                      # Task Scheduler setup + manual command reference
├── requirements.txt              # Python dependencies (pip install -r)
├── run.bat                       # Task Scheduler entry point: start server → run main.py
│
├── main.py                       # Orchestrator: CLI args, pipeline coordination, logging setup
│
├── agents/
│   ├── __init__.py
│   └── extractor_scorer.py       # Claude API wrapper: RawJob → JobPosting (Pydantic)
│                                 #   + backfill_deadlines() for retroactive deadline extraction
│
├── config/
│   ├── profile.yaml              # AUTHORITATIVE scoring system prompt + Timo's profile
│   └── settings.yaml            # Runtime config: scraper limits, score thresholds, logging
│
├── db/
│   ├── __init__.py
│   ├── dedup.py                  # All DB operations: connect, is_seen, insert_job,
│   │                             #   mark_emailed, log_run_start/finish, etc.
│   ├── jobs.db                   # SQLite database file (gitignored)
│   ├── migrations.py             # init_db(): idempotent setup, safe ALTER TABLE migrations
│   └── schema.sql               # DDL: jobs, feedback, failed_extractions, run_log tables
│
├── feedback/
│   ├── __init__.py
│   ├── .server.pid               # PID file written by server.py at startup (runtime artifact)
│   ├── feedback_store.json       # Flat JSON log of like/pass actions (for prompt calibration)
│   ├── profile_updater.py        # generate_prompt_additions() + build_feedback_footer_html()
│   ├── server.py                 # Flask app (port 5001): job browser + feedback form
│   └── store.py                  # Read/write helpers for feedback_store.json
│
├── logs/                         # All log files (gitignored)
│   ├── scraper.log               # Rotating (5 MB, 3 backups) — main.py output
│   ├── server.log                # Rotating (2 MB, 2 backups) — Flask server output
│   └── task_scheduler_output.log # stdout/stderr from run.bat via Task Scheduler
│
├── notifier/
│   ├── __init__.py
│   └── gmail.py                  # Build HTML email + send via Gmail SMTP SSL (port 465)
│                                 #   Daily digest (score>=8) + Weekly digest (all jobs)
│
└── scrapers/
    ├── __init__.py
    ├── base.py                   # RawJob dataclass; BaseScraper + PlaywrightBaseScraper ABCs
    │
    ├── academictransfer.py       # PhD + postdoc from academictransfer.com (paginated, requests)
    ├── bit.py                    # DISABLED — Cloudflare block; 0 open positions at audit
    ├── busara.py                 # Busara Center — Lever ATS JSON API
    ├── case_poland.py            # CASE Poland — HTML scraping (requests + BS4)
    ├── eucareers.py              # EU agency traineeships — Playwright; seasonal (Mar/Oct)
    ├── euraxess.py               # EURAXESS research jobs — ECL article cards (requests)
    ├── fgv.py                    # DISABLED — original domain defunct
    ├── impactpool.py             # Impactpool — server-rendered cards (requests + BS4)
    ├── jrc.py                    # JRC PhD positions — h3-based parsing (requests + BS4)
    ├── oecd.py                   # DISABLED — Cloudflare bot challenge
    ├── rand.py                   # RAND Corporation — Workday CXS JSON API (POST)
    ├── scp.py                    # SCP vacancies — werkenvoornederland.nl Bloomreach endpoint
    ├── tni.py                    # TNI — requests; always returns 429 (IP-level block)
    ├── trimbos.py                # Trimbos-instituut — Playwright; JS-rendered SPA
    ├── uncareers.py              # DISABLED — CloudFront 403 block
    └── wodc.py                   # WODC vacancies — werkenvoornederland.nl Bloomreach endpoint
```

---

## Configuration Reference

### `config/settings.yaml`

```yaml
scraper:
  request_delay_seconds: 2        # Seconds to sleep between paginated requests (Euraxess, AcademicTransfer)
  playwright_timeout_ms: 30000    # Legacy — no longer used; _async_fetch uses hardcoded 15000
  max_jobs_per_site: 100          # Cap on jobs collected per scraper per run
  raw_text_max_chars: 4000        # Max chars of raw_text passed to Claude (env var RAW_TEXT_MAX_CHARS overrides)

filtering:
  strong_match_threshold: 8      # Score >= this → "Strong Matches" in daily email; read by gmail.py
  maybe_threshold: 5             # Score >= this → "maybe" tier in DB/UI (not the email cutoff)
  email_also_min_score: 6        # Score >= this → "Also Found" section in daily email; read by gmail.py
  # All three values are read by notifier/gmail.py:_load_thresholds() at startup.
  # Changing these values takes effect on the next main.py run.

email:
  send_if_no_new_jobs: true      # Currently unused — the actual gate is should_send_daily() in gmail.py
  max_jobs_in_email: 50          # Currently unused — no hard cap in send_digest()

logging:
  level: INFO                    # Root logger level (DEBUG | INFO | WARNING | ERROR)
  max_bytes: 5242880             # 5 MB rotating log for scraper.log
  backup_count: 3                # Keep 3 rotated log files
```

### `config/profile.yaml`

Contains the `system_prompt:` key with the full scoring instructions for Claude. This is what controls scoring behaviour. Key sections:
- **TIMO'S PROFILE** — education, skills, languages, target sectors/roles
- **HARD DISQUALIFIERS** — postdoc (requires PhD = max score 2), 5+ years XP, medical degree
- **STRONG PENALTIES** — senior roles (−2–3), climate focus (−2), communication roles (−3)
- **PhD POSITION CLARIFICATION** — PhD student positions do NOT require a completed PhD; score 6–9
- **SCORING SCALE** — explicit score-to-meaning mapping (8–10 apply now; 6–7 worth considering; etc.)
- **BONUS POINTS** — Amsterdam/NL-based, EU/UN, drug policy, quantitative methods, Dutch language

### `.env` (gitignored — copy from `.env.example`)

```
ANTHROPIC_API_KEY=sk-ant-...          # Required; from console.anthropic.com
CLAUDE_MODEL=claude-haiku-4-5-20251001 # Optional; defaults to this value if not set
GMAIL_ADDRESS=you@gmail.com           # Required; Gmail account used to send
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx # Required; App Password (not your Gmail password)
NOTIFY_RECIPIENT=you@gmail.com        # Optional; defaults to GMAIL_ADDRESS if not set
RAW_TEXT_MAX_CHARS=4000               # Optional; max chars of job text passed to Claude
```

---

## Known Limitations & Disabled Scrapers

### Disabled scrapers (return 0 results, log a WARNING)

| Scraper | Reason | GitHub Issue |
|---|---|---|
| `tni.py` | HTTP 429 on every request — IP-level rate limit, not UA-based | [#1](https://github.com/timovanommeren/job_scraper/issues/1) |
| `uncareers.py` | CloudFront (AWS CDN) returns HTTP 403 to all automation | [#2](https://github.com/timovanommeren/job_scraper/issues/2) |
| `oecd.py` | Cloudflare bot challenge before any content loads | [#3](https://github.com/timovanommeren/job_scraper/issues/3) |
| `bit.py` | Cloudflare block; also confirmed 0 open positions at audit | [#4](https://github.com/timovanommeren/job_scraper/issues/4) |
| `fgv.py` | Original URL defunct (HTTP 404 + SSL errors) | [#5](https://github.com/timovanommeren/job_scraper/issues/5) |

### Seasonal scrapers

| Scraper | Notes |
|---|---|
| `eucareers.py` | EU traineeships open ~March and October only. Returns 0 outside intake windows — this is expected. Logs an info message when 0 found. |

### Missing Blue Book traineeship coverage

The EU Commission Blue Book traineeship (EPSO-managed, distinct from agency traineeships) is not covered. See [#6](https://github.com/timovanommeren/job_scraper/issues/6).

### Code-level gotchas to be aware of

1. **`db/dedup.py:get_unemailed_jobs()`** is defined but never called anywhere in the application. It is dead code. The weekly digest queries the DB directly in `gmail.py:send_weekly_digest()`.

2. **`.env.example` model name is stale.** It shows `CLAUDE_MODEL=claude-haiku-3-5-20251001` but the code default is `claude-haiku-4-5-20251001`. If you copy `.env.example` verbatim, you'll be using a potentially outdated model name.

3. **Weekly digest does not deduplicate against daily emails.** A job emailed on Monday's daily digest will also appear in Tuesday's weekly digest. The weekly digest queries `first_seen_at >= 7 days ago` with no `emailed_at IS NULL` filter. This is by design (weekly = retrospective), but worth knowing.
