"""
Tests for T3: content_fingerprint() and the L2 content_hash path in is_seen().

Run: python -m pytest tests/test_content_hash_dedup.py -v
"""
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.dedup import content_fingerprint, fingerprint, is_seen


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def mem_conn():
    """In-memory SQLite with jobs + filtered_jobs tables including content_hash."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        PRAGMA foreign_keys = ON;

        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL UNIQUE,
            url_hash TEXT NOT NULL UNIQUE,
            content_hash TEXT,
            source TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );

        CREATE TABLE filtered_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            url_hash TEXT NOT NULL UNIQUE,
            source TEXT NOT NULL,
            filter_stage TEXT NOT NULL,
            filter_reason TEXT,
            filtered_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
    """)
    return conn


def _insert_job(conn, url, title, org, source="test"):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO jobs (url, url_hash, content_hash, source, first_seen_at, last_seen_at) "
        "VALUES (?,?,?,?,?,?)",
        (url, fingerprint(url), content_fingerprint(title, org), source, now, now),
    )
    conn.commit()


# ── content_fingerprint unit tests ─────────────────────────────────────────────

class TestContentFingerprint:

    def test_identical_inputs_produce_same_hash(self):
        h1 = content_fingerprint("PhD in Sociology", "Utrecht University")
        h2 = content_fingerprint("PhD in Sociology", "Utrecht University")
        assert h1 == h2

    def test_dash_and_colon_normalise_the_same(self):
        # "PhD Candidate - Methodology" vs "PhD Candidate: Methodology"
        h1 = content_fingerprint("PhD Candidate - Methodology & Statistics", "Utrecht University")
        h2 = content_fingerprint("PhD Candidate: Methodology & Statistics", "Utrecht University")
        assert h1 == h2, "Punctuation variants of the same title should hash identically"

    def test_case_insensitive(self):
        h1 = content_fingerprint("PhD in SOCIOLOGY", "Utrecht University")
        h2 = content_fingerprint("phd in sociology", "UTRECHT UNIVERSITY")
        assert h1 == h2

    def test_different_org_produces_different_hash(self):
        h1 = content_fingerprint("PhD in Political Science", "Utrecht University")
        h2 = content_fingerprint("PhD in Political Science", "Leiden University")
        assert h1 != h2

    def test_different_title_produces_different_hash(self):
        h1 = content_fingerprint("PhD in Sociology", "Utrecht University")
        h2 = content_fingerprint("Postdoc in Sociology", "Utrecht University")
        assert h1 != h2

    def test_empty_inputs_do_not_raise(self):
        h = content_fingerprint("", "")
        assert len(h) == 64  # SHA-256 hex digest

    def test_none_like_empty_string_safe(self):
        # content_fingerprint receives raw.title / raw.organization which may be ""
        h = content_fingerprint("", "Utrecht University")
        assert isinstance(h, str) and len(h) == 64

    def test_returns_64_char_hex_string(self):
        h = content_fingerprint("Some Title", "Some Org")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


# ── is_seen() L2 content_hash tests ───────────────────────────────────────────

class TestIsSeenContentHash:

    def test_cross_source_duplicate_detected(self, mem_conn):
        """Same title+org from a different URL should be detected as 'scored'."""
        _insert_job(mem_conn, "https://uu.nl/job/123", "PhD in Sociology", "Utrecht University")

        # Completely different URL (from AcademicTransfer), same job
        result = is_seen(
            "https://academictransfer.com/en/jobs/99999",
            mem_conn,
            title="PhD in Sociology",
            org="Utrecht University",
        )
        assert result == "scored", "Cross-source duplicate should be detected as already scored"

    def test_different_job_same_org_is_new(self, mem_conn):
        _insert_job(mem_conn, "https://uu.nl/job/123", "PhD in Sociology", "Utrecht University")

        result = is_seen(
            "https://academictransfer.com/en/jobs/77777",
            mem_conn,
            title="Postdoc in Economics",
            org="Utrecht University",
        )
        assert result == "new"

    def test_same_title_different_org_is_new(self, mem_conn):
        _insert_job(mem_conn, "https://uu.nl/job/123", "PhD in Political Science", "Utrecht University")

        result = is_seen(
            "https://leidenuniv.nl/job/456",
            mem_conn,
            title="PhD in Political Science",
            org="Leiden University",
        )
        assert result == "new"

    def test_no_title_skips_l2_check(self, mem_conn):
        """Omitting title falls back to URL-only dedup (backward compatibility)."""
        _insert_job(mem_conn, "https://uu.nl/job/123", "PhD in Sociology", "Utrecht University")

        # No title provided — is_seen should not check content_hash
        result = is_seen("https://academictransfer.com/en/jobs/99999", mem_conn)
        assert result == "new", "Without title, L2 should be skipped"

    def test_l1_url_hash_still_works(self, mem_conn):
        """L1 (URL-hash) check remains functional alongside L2."""
        _insert_job(mem_conn, "https://uu.nl/job/123", "PhD in Sociology", "Utrecht University")

        result = is_seen("https://uu.nl/job/123", mem_conn)
        assert result == "scored"

    def test_punctuation_variants_detected_as_duplicate(self, mem_conn):
        """Dash vs colon in title should still hash to the same content_hash."""
        _insert_job(
            mem_conn,
            "https://uu.nl/job/123",
            "PhD Candidate - Methodology & Statistics",
            "Utrecht University",
        )

        result = is_seen(
            "https://academictransfer.com/en/jobs/55555",
            mem_conn,
            title="PhD Candidate: Methodology & Statistics",
            org="Utrecht University",
        )
        assert result == "scored", "Punctuation variants of same title should be caught by L2"

    def test_l2_skipped_gracefully_when_column_absent(self, mem_conn):
        """If content_hash column is missing (pre-migration DB), is_seen fails open."""
        # Drop the content_hash column from the fixture
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
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
                filter_stage TEXT NOT NULL,
                filter_reason TEXT,
                filtered_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
        """)
        # Should not raise even without content_hash column
        result = is_seen("https://example.com/new", conn, title="PhD in Sociology", org="UU")
        assert result == "new"
