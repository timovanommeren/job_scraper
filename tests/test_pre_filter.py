"""
Tests for Layer 2 pre-filter: pre_screen() and the extended is_seen().

Run: python -m pytest tests/test_pre_filter.py -v
"""
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scrapers.base import RawJob


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def mem_conn():
    """In-memory SQLite with the minimal schema needed for these tests."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        PRAGMA foreign_keys = ON;

        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL UNIQUE,
            url_hash TEXT NOT NULL UNIQUE,
            source TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );

        CREATE TABLE filtered_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            url_hash TEXT NOT NULL UNIQUE,
            source TEXT NOT NULL,
            title TEXT,
            organization TEXT,
            raw_text TEXT,
            filter_stage TEXT NOT NULL,
            filter_reason TEXT,
            similarity REAL,
            filtered_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
    """)
    conn.commit()
    return conn


@pytest.fixture
def raw_job_infield():
    return RawJob(
        title="Research Analyst — Drug Policy",
        url="https://example.com/job/infield",
        source="test",
        raw_text="We are looking for a quantitative researcher with experience in public health "
                 "and social science methods. The role involves statistical modelling and policy analysis.",
    )


@pytest.fixture
def raw_job_outfield():
    return RawJob(
        title="Marine Biologist",
        url="https://example.com/job/outfield",
        source="test",
        raw_text="Study coral reef ecosystems and marine biodiversity in the Pacific Ocean. "
                 "PhD in marine biology or oceanography required.",
    )


def _make_mock_client(relevant: bool, reason: str = "Test reason."):
    """Build an instructor-style mock client that returns a _PreScreenResult."""
    from agents.extractor_scorer import _PreScreenResult
    mock = MagicMock()
    mock.chat.completions.create.return_value = _PreScreenResult(
        relevant=relevant, reason=reason
    )
    return mock


# ── pre_screen tests ───────────────────────────────────────────────────────────

class TestPreScreen:

    def test_returns_true_for_infield_job(self, raw_job_infield):
        from agents.extractor_scorer import pre_screen
        client = _make_mock_client(relevant=True, reason="Social science and public health role.")
        passed, reason = pre_screen(raw_job_infield, client)
        assert passed is True
        assert "Social science" in reason

    def test_returns_false_for_outfield_job(self, raw_job_outfield):
        from agents.extractor_scorer import pre_screen
        client = _make_mock_client(relevant=False, reason="Marine biology is not in the target domain.")
        passed, reason = pre_screen(raw_job_outfield, client)
        assert passed is False
        assert reason != "pre_screen_error"

    def test_fail_open_on_api_exception(self, raw_job_infield):
        from agents.extractor_scorer import pre_screen
        client = MagicMock()
        client.chat.completions.create.side_effect = Exception("API down")
        passed, reason = pre_screen(raw_job_infield, client)
        assert passed is True
        assert reason == "pre_screen_error"


# ── is_seen tests ──────────────────────────────────────────────────────────────

class TestIsSeen:

    def _insert_job(self, conn, url):
        from db.dedup import fingerprint
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO jobs (url, url_hash, source, first_seen_at, last_seen_at) VALUES (?,?,?,?,?)",
            (url, fingerprint(url), "test", now, now),
        )
        conn.commit()

    def _insert_filtered(self, conn, url, expires_delta_days=30):
        from db.dedup import fingerprint
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        exp = (datetime.now(timezone.utc) + timedelta(days=expires_delta_days)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """INSERT INTO filtered_jobs
               (url, url_hash, source, filter_stage, filter_reason, filtered_at, expires_at)
               VALUES (?,?,?,?,?,?,?)""",
            (url, fingerprint(url), "test", "pre_screen", "out of field", now_str, exp),
        )
        conn.commit()

    def test_returns_scored_for_known_url(self, mem_conn):
        from db.dedup import is_seen
        url = "https://example.com/known"
        self._insert_job(mem_conn, url)
        assert is_seen(url, mem_conn) == "scored"

    def test_returns_filtered_for_non_expired_filtered_url(self, mem_conn):
        from db.dedup import is_seen
        url = "https://example.com/filtered"
        self._insert_filtered(mem_conn, url, expires_delta_days=30)
        assert is_seen(url, mem_conn) == "filtered"

    def test_returns_new_for_unseen_url(self, mem_conn):
        from db.dedup import is_seen
        assert is_seen("https://example.com/brand-new", mem_conn) == "new"

    def test_returns_new_for_expired_filtered_url(self, mem_conn):
        from db.dedup import is_seen
        url = "https://example.com/expired"
        self._insert_filtered(mem_conn, url, expires_delta_days=-1)  # expired yesterday
        assert is_seen(url, mem_conn) == "new"
