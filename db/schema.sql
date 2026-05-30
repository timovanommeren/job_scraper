PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS jobs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    url                 TEXT    NOT NULL UNIQUE,
    url_hash            TEXT    NOT NULL UNIQUE,
    source              TEXT    NOT NULL,
    title               TEXT,
    organization        TEXT,
    location            TEXT,
    contract_type       TEXT,
    deadline            TEXT,
    description_snippet TEXT,
    tags                TEXT,               -- JSON array: '["statistics","policy"]'
    relevance_score     INTEGER,
    relevance_tier      TEXT,               -- 'strong_match' | 'maybe' | 'not_relevant'
    relevance_reason    TEXT,
    raw_text            TEXT,               -- preserved for reprocessing
    first_seen_at       TEXT    NOT NULL,   -- ISO 8601: '2026-05-28T07:00:00'
    last_seen_at        TEXT    NOT NULL,
    emailed_at          TEXT,               -- NULL = not yet emailed
    is_active           INTEGER DEFAULT 1   -- 0 = no longer found on site
);

CREATE TABLE IF NOT EXISTS failed_extractions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    url         TEXT    NOT NULL,
    source      TEXT,
    raw_text    TEXT,
    error_msg   TEXT,
    created_at  TEXT    NOT NULL,
    retried     INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS run_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at          TEXT    NOT NULL,
    finished_at         TEXT,
    sites_scraped       INTEGER DEFAULT 0,
    new_jobs_found      INTEGER DEFAULT 0,
    jobs_scored         INTEGER DEFAULT 0,
    jobs_filtered       INTEGER DEFAULT 0,
    jobs_emailed        INTEGER DEFAULT 0,
    api_errors          INTEGER DEFAULT 0,
    pre_screen_errors   INTEGER DEFAULT 0,
    status              TEXT    -- 'success' | 'partial' | 'failed'
);

CREATE INDEX IF NOT EXISTS idx_jobs_url_hash     ON jobs(url_hash);
CREATE INDEX IF NOT EXISTS idx_jobs_source       ON jobs(source);
CREATE INDEX IF NOT EXISTS idx_jobs_emailed_at   ON jobs(emailed_at);
CREATE INDEX IF NOT EXISTS idx_jobs_tier         ON jobs(relevance_tier);

-- ── Structured user feedback ──────────────────────────────────────────────────
-- job_id stores the integer jobs.id as text (SQLite FK across TEXT/INTEGER is
-- advisory here; enforced in application logic instead).
CREATE TABLE IF NOT EXISTS feedback (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id           TEXT    NOT NULL,
    relevance_score  INTEGER CHECK(relevance_score BETWEEN 1 AND 10),
    mismatch_reasons TEXT,           -- legacy column; superseded by tags (added via migrations.py). Kept for compat.
    comment          TEXT,           -- free-text, nullable
    timestamp        DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_feedback_job_id   ON feedback(job_id);
CREATE INDEX IF NOT EXISTS idx_feedback_ts       ON feedback(timestamp);

-- ── Source suggestion tracking ────────────────────────────────────────────────
-- Stores organisations suggested by the weekly field-intelligence recommender.
-- status: 'pending' (not yet actioned) | 'skipped' (user dismissed via email link)
-- Adding a scraper is a manual code edit; there is no 'added' status.
-- ── Pre-filter audit log ──────────────────────────────────────────────────────
-- Jobs rejected before LLM scoring. Training data (negatives) for future ranker.
-- expires_at: is_seen() treats this row as 'new' after expiry (allows re-scrape).
-- NOTE (A3): expires_at is stored in space-separated SQLite format
--   (strftime('%Y-%m-%d %H:%M:%S', 'now', '+30 days')) so the string comparison
--   expires_at > datetime('now') stays within one format.
CREATE TABLE IF NOT EXISTS filtered_jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    url           TEXT    NOT NULL,
    url_hash      TEXT    NOT NULL UNIQUE,
    source        TEXT    NOT NULL,
    title         TEXT,
    organization  TEXT,
    raw_text      TEXT,              -- first 4000 chars; required for ranker training features
    filter_stage  TEXT    NOT NULL,  -- 'url_search' | 'pre_screen' | 'embedding'
    filter_reason TEXT,              -- pre_screen reason sentence or similarity score
    similarity    REAL,              -- populated for embedding approach only
    filtered_at   TEXT    NOT NULL,
    expires_at    TEXT    NOT NULL   -- space-separated SQLite format; is_seen returns 'new' after this
);
CREATE INDEX IF NOT EXISTS idx_filtered_url_hash ON filtered_jobs(url_hash);
CREATE INDEX IF NOT EXISTS idx_filtered_source   ON filtered_jobs(source);

-- ── Implicit view signal (weak positive for ranker training) ──────────────────
CREATE TABLE IF NOT EXISTS job_views (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id    INTEGER NOT NULL,
    viewed_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_views_job_id ON job_views(job_id);

CREATE TABLE IF NOT EXISTS source_suggestions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    suggested_at     TEXT    NOT NULL,
    org_name         TEXT    NOT NULL,
    org_country      TEXT,
    org_description  TEXT,
    careers_url      TEXT,
    status           TEXT    DEFAULT 'pending',
    skipped_at       TEXT
);
