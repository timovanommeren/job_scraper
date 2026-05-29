"""
Tests for feedback/source_recommender.py.

Run: python -m pytest tests/test_source_recommender.py -v
"""
import json
import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_db():
    """In-memory SQLite with the schema tables needed by source_recommender."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT, organization TEXT, location TEXT, source TEXT,
            relevance_score INTEGER, tags TEXT, relevance_reason TEXT,
            first_seen_at TEXT NOT NULL DEFAULT '2026-05-01T00:00:00'
        );
        CREATE TABLE feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            relevance_score INTEGER,
            tags TEXT,
            comment TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE source_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suggested_at TEXT NOT NULL,
            org_name TEXT NOT NULL,
            org_country TEXT,
            org_description TEXT,
            careers_url TEXT,
            status TEXT DEFAULT 'pending',
            skipped_at TEXT
        );
    """)
    return conn


def _insert_job(conn, score=9, org="Test Org", source="testsite"):
    conn.execute(
        "INSERT INTO jobs (title, organization, location, source, relevance_score, tags, first_seen_at) "
        "VALUES (?, ?, 'Amsterdam', ?, ?, '[]', datetime('now'))",
        ("Research Analyst", org, source, score),
    )
    conn.commit()


# ── get_high_rated_jobs ────────────────────────────────────────────────────────

class TestGetHighRatedJobs:

    def test_returns_empty_when_no_jobs(self):
        from feedback.source_recommender import get_high_rated_jobs
        conn = _make_db()
        assert get_high_rated_jobs(conn) == []

    def test_returns_jobs_above_threshold(self):
        from feedback.source_recommender import get_high_rated_jobs
        conn = _make_db()
        _insert_job(conn, score=9)
        _insert_job(conn, score=5)  # below threshold
        result = get_high_rated_jobs(conn, min_score=8)
        assert len(result) == 1
        assert result[0]["effective_score"] == 9

    def test_user_feedback_score_overrides_llm_score(self):
        from feedback.source_recommender import get_high_rated_jobs
        conn = _make_db()
        # Job scored 6 by LLM but user gave 9
        conn.execute(
            "INSERT INTO jobs (title, organization, source, relevance_score, first_seen_at) "
            "VALUES ('Job', 'Org', 'src', 6, datetime('now'))"
        )
        job_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO feedback (job_id, relevance_score) VALUES (?, 9)",
            (str(job_id),),
        )
        conn.commit()
        result = get_high_rated_jobs(conn, min_score=8)
        assert len(result) == 1
        assert result[0]["effective_score"] == 9

    def test_excludes_old_jobs_outside_lookback(self):
        from feedback.source_recommender import get_high_rated_jobs
        conn = _make_db()
        conn.execute(
            "INSERT INTO jobs (title, organization, source, relevance_score, first_seen_at) "
            "VALUES ('Old Job', 'Org', 'src', 9, '2020-01-01T00:00:00')"
        )
        conn.commit()
        result = get_high_rated_jobs(conn, min_score=8, lookback_days=90)
        assert len(result) == 0


# ── generate_suggestions threshold guard ──────────────────────────────────────

class TestGenerateSuggestionsThreshold:

    def test_returns_none_below_min_jobs(self):
        from feedback import source_recommender
        conn = _make_db()
        _insert_job(conn, score=9)  # only 1, threshold is 5
        with patch.object(source_recommender, "_load_min_jobs", return_value=5):
            result = source_recommender.generate_suggestions(conn)
        assert result is None

    def test_proceeds_when_at_or_above_min_jobs(self):
        from feedback import source_recommender
        conn = _make_db()
        for _ in range(5):
            _insert_job(conn, score=9)
        mock_client = MagicMock()
        mock_recommendation = MagicMock()
        mock_recommendation.suggestions = []
        mock_recommendation.profile_summary = "Test profile"
        with patch.object(source_recommender, "_load_min_jobs", return_value=5):
            with patch.object(source_recommender, "analyze_and_suggest", return_value=mock_recommendation):
                result = source_recommender.generate_suggestions(conn, client=mock_client)
        assert result is not None


# ── validate_url ──────────────────────────────────────────────────────────────

class TestValidateUrl:

    def _make_suggestion(self, url="https://example.org/careers"):
        from feedback.source_recommender import OrgSuggestion
        return OrgSuggestion(
            name="Test Org", country="Netherlands",
            description="A test org.", candidate_url=url,
        )

    def test_returns_true_on_200(self):
        from feedback import source_recommender
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.url = "https://example.org/careers"
        with patch("feedback.source_recommender.requests.get", return_value=mock_resp):
            s = self._make_suggestion()
            result = source_recommender.validate_url(s)
        assert result is True
        assert s.validated_url == "https://example.org/careers"

    def test_falls_back_to_root_domain_on_404(self):
        from feedback import source_recommender
        resp_404 = MagicMock()
        resp_404.status_code = 404
        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.url = "https://example.org"

        call_count = [0]
        def _get(url, **kwargs):
            call_count[0] += 1
            return resp_404 if call_count[0] == 1 else resp_ok

        with patch("feedback.source_recommender.requests.get", side_effect=_get):
            s = self._make_suggestion("https://example.org/careers")
            result = source_recommender.validate_url(s)
        assert result is True
        assert s.validated_url == "https://example.org"

    def test_returns_false_when_all_steps_fail(self):
        from feedback import source_recommender
        import requests as req_lib
        with patch("feedback.source_recommender.requests.get", side_effect=req_lib.exceptions.ConnectionError):
            s = self._make_suggestion("https://nonexistent.invalid/careers")
            result = source_recommender.validate_url(s)
        assert result is False
        assert s.validated_url is None

    def test_does_not_set_validated_url_on_500(self):
        from feedback import source_recommender
        resp_500 = MagicMock()
        resp_500.status_code = 500

        def _get(url, **kwargs):
            # root domain also fails — force DDG path which also raises
            raise Exception("all fail")

        with patch("feedback.source_recommender.requests.get", side_effect=Exception("fail")):
            s = self._make_suggestion()
            result = source_recommender.validate_url(s)
        assert result is False


# ── analyze_and_suggest ───────────────────────────────────────────────────────

class TestAnalyzeAndSuggest:

    def _jobs(self):
        return [{"title": "Researcher", "organization": "RAND", "source": "rand",
                 "effective_score": 9, "tags": '["policy"]'}]

    def test_returns_recommendation_on_success(self):
        from feedback import source_recommender
        from feedback.source_recommender import OrgSuggestion, SourceRecommendation
        expected = SourceRecommendation(
            profile_summary="Strong fit for EU policy institutes.",
            suggestions=[OrgSuggestion(name="CPB", country="Netherlands",
                                       description="Economic analysis.",
                                       candidate_url="https://cpb.nl/vacatures")],
        )
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = expected
        result = source_recommender.analyze_and_suggest(self._jobs(), [], [], mock_client)
        assert result.profile_summary == "Strong fit for EU policy institutes."
        assert len(result.suggestions) == 1

    def test_returns_empty_suggestion_list_on_validation_error(self):
        from feedback import source_recommender
        mock_client = MagicMock()
        # Patch ValidationError to Exception so the except ValidationError clause is triggered
        with patch("feedback.source_recommender.ValidationError", Exception):
            mock_client.chat.completions.create.side_effect = Exception("mocked_validation_error")
            result = source_recommender.analyze_and_suggest(self._jobs(), [], [], mock_client)
        assert result.suggestions == []


# ── generate_suggestions DB writes ────────────────────────────────────────────

class TestGenerateSuggestionsDbWrites:

    def test_saves_validated_suggestions_to_db(self):
        from feedback import source_recommender
        from feedback.source_recommender import OrgSuggestion, SourceRecommendation
        conn = _make_db()
        for _ in range(5):
            _insert_job(conn, score=9)

        suggestion = OrgSuggestion(
            name="CPB Netherlands Bureau", country="Netherlands",
            description="Econ policy institute.", candidate_url="https://cpb.nl/vacatures",
            validated_url="https://cpb.nl/vacatures",
        )
        mock_rec = SourceRecommendation(profile_summary="Policy focus", suggestions=[suggestion])

        with patch.object(source_recommender, "_load_min_jobs", return_value=5):
            with patch.object(source_recommender, "analyze_and_suggest", return_value=mock_rec):
                with patch.object(source_recommender, "validate_url", return_value=True):
                    result = source_recommender.generate_suggestions(conn, client=MagicMock(), test_mode=False)

        assert result is not None
        row = conn.execute("SELECT * FROM source_suggestions WHERE org_name = 'CPB Netherlands Bureau'").fetchone()
        assert row is not None
        assert row["status"] == "pending"

    def test_does_not_write_to_db_in_test_mode(self):
        from feedback import source_recommender
        from feedback.source_recommender import OrgSuggestion, SourceRecommendation
        conn = _make_db()
        for _ in range(5):
            _insert_job(conn, score=9)

        suggestion = OrgSuggestion(
            name="CPB Netherlands Bureau", country="Netherlands",
            description="Econ policy institute.", candidate_url="https://cpb.nl/vacatures",
            validated_url="https://cpb.nl/vacatures",
        )
        mock_rec = SourceRecommendation(profile_summary="Policy focus", suggestions=[suggestion])

        with patch.object(source_recommender, "_load_min_jobs", return_value=5):
            with patch.object(source_recommender, "analyze_and_suggest", return_value=mock_rec):
                with patch.object(source_recommender, "validate_url", return_value=True):
                    source_recommender.generate_suggestions(conn, client=MagicMock(), test_mode=True)

        count = conn.execute("SELECT COUNT(*) FROM source_suggestions").fetchone()[0]
        assert count == 0

    def test_excludes_suggestions_with_failed_url_validation(self):
        from feedback import source_recommender
        from feedback.source_recommender import OrgSuggestion, SourceRecommendation
        conn = _make_db()
        for _ in range(5):
            _insert_job(conn, score=9)

        s1 = OrgSuggestion(name="Good Org", country="DE", description="Good.",
                            candidate_url="https://good.org/jobs", validated_url="https://good.org/jobs")
        s2 = OrgSuggestion(name="Bad Org", country="BE", description="Bad URL.",
                            candidate_url="https://bad.invalid/careers")
        mock_rec = SourceRecommendation(profile_summary="Mixed", suggestions=[s1, s2])

        def _validate(s):
            if s.name == "Good Org":
                return True
            return False

        with patch.object(source_recommender, "_load_min_jobs", return_value=5):
            with patch.object(source_recommender, "analyze_and_suggest", return_value=mock_rec):
                with patch.object(source_recommender, "validate_url", side_effect=_validate):
                    result = source_recommender.generate_suggestions(conn, client=MagicMock())

        assert result is not None
        assert len(result.suggestions) == 1
        assert result.suggestions[0].name == "Good Org"

    def test_returns_none_on_unexpected_exception(self):
        from feedback import source_recommender
        conn = _make_db()
        for _ in range(5):
            _insert_job(conn, score=9)
        with patch.object(source_recommender, "_load_min_jobs", return_value=5):
            with patch.object(source_recommender, "analyze_and_suggest", side_effect=RuntimeError("boom")):
                result = source_recommender.generate_suggestions(conn, client=MagicMock())
        assert result is None

    def test_skipped_orgs_excluded_from_suggestions(self):
        from feedback import source_recommender
        conn = _make_db()
        for _ in range(5):
            _insert_job(conn, score=9)
        # Insert a previously skipped org
        conn.execute(
            "INSERT INTO source_suggestions (suggested_at, org_name, status, skipped_at) "
            "VALUES (datetime('now'), 'Skipped Org', 'skipped', datetime('now'))"
        )
        conn.commit()

        captured_excluded = []

        def _capture_analyze(jobs, excluded, covered, client):
            captured_excluded.extend(excluded)
            from feedback.source_recommender import SourceRecommendation
            return SourceRecommendation(profile_summary="p", suggestions=[])

        with patch.object(source_recommender, "_load_min_jobs", return_value=5):
            with patch.object(source_recommender, "analyze_and_suggest", side_effect=_capture_analyze):
                source_recommender.generate_suggestions(conn, client=MagicMock())

        assert "Skipped Org" in captured_excluded
