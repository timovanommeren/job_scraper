"""
Tests for the criteria slider feedback form in feedback/server.py.

Run: python -m pytest tests/test_flask_tags.py -v
"""
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

_EMPTY_SUMMARY = {"liked": [], "passed": [], "applied": [], "total": 0}


def _make_test_db(db_path: Path) -> None:
    """Create a minimal SQLite DB with one job for testing."""
    con = sqlite3.connect(str(db_path))
    con.execute("""CREATE TABLE jobs (
        id INTEGER PRIMARY KEY, url TEXT, title TEXT, organization TEXT,
        location TEXT, source TEXT, relevance_score INTEGER, relevance_tier TEXT,
        relevance_reason TEXT, description_snippet TEXT, contract_type TEXT,
        deadline TEXT, tags TEXT, first_seen_at TEXT
    )""")
    con.execute("""CREATE TABLE feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT,
        relevance_score INTEGER, tags TEXT, comment TEXT,
        mismatch_reasons TEXT, criteria TEXT,
        timestamp TEXT DEFAULT (datetime('now'))
    )""")
    con.execute(
        "INSERT INTO jobs VALUES (1,'https://ex.com/1','Test PhD','RAND','Brussels',"
        "'test',8,'strong_match','Good fit','Snippet','Full-time',NULL,'[]','2026-05-29')"
    )
    con.commit()
    con.close()


@pytest.fixture()
def client(tmp_path):
    """Flask test client backed by a temp SQLite DB with one job."""
    db_path = tmp_path / "test.db"
    _make_test_db(db_path)

    import feedback.server as srv
    from flask import g

    def _mock_get_db():
        if "db" not in g:
            con = sqlite3.connect(str(db_path))
            con.row_factory = sqlite3.Row
            g.db = con
        return g.db

    with patch.object(srv, "_get_db", _mock_get_db):
        with patch("feedback.store.add_feedback"):
            with patch("feedback.profile_updater.update_liked_organizations"):
                with patch("feedback.store.get_feedback_summary",
                           return_value=_EMPTY_SUMMARY):
                    srv.app.config["TESTING"] = True
                    yield srv.app.test_client(), db_path


class TestCriteriaFormSubmit:

    def test_submit_redirects_to_feedback_saved(self, client):
        """POST /jobs/1/feedback returns 302 to /jobs?feedback=saved."""
        tc, _ = client
        resp = tc.post("/jobs/1/feedback", data={
            "criteria_topic_fit": "4",
            "criteria_methods_fit": "4",
            "criteria_org_appeal": "5",
            "criteria_career_fit": "4",
            "criteria_location_fit": "4",
            "comment": "Looks great",
        })
        assert resp.status_code == 302
        location = resp.headers["Location"]
        assert "feedback=saved" in location

    def test_feedback_saved_banner_displayed(self, client):
        """GET /jobs?feedback=saved renders the green confirmation banner."""
        tc, _ = client
        resp = tc.get("/jobs?feedback=saved")
        assert resp.status_code == 200
        assert b"Feedback saved" in resp.data

    def test_submit_defaults_succeeds(self, client):
        """POST with no criteria fields completes without error (defaults to 3)."""
        tc, _ = client
        resp = tc.post("/jobs/1/feedback", data={"comment": "OK job"})
        assert resp.status_code == 302

    def test_criteria_stored_via_write_feedback_sqlite(self, client):
        """Criterion slider values reach _write_feedback_sqlite as a dict."""
        tc, _ = client

        import feedback.server as srv

        stored = {}

        def capture(job_id, relevance_score, tags, comment, criteria=None):
            stored["criteria"] = criteria
            stored["score"] = relevance_score

        with patch.object(srv, "_write_feedback_sqlite", side_effect=capture):
            tc.post("/jobs/1/feedback", data={
                "criteria_topic_fit": "5",
                "criteria_methods_fit": "5",
                "criteria_org_appeal": "5",
                "criteria_career_fit": "5",
                "criteria_location_fit": "5",
            })

        assert stored.get("criteria") == {
            "topic_fit": 5, "methods_fit": 5, "org_appeal": 5,
            "career_fit": 5, "location_fit": 5,
        }
        assert stored.get("score") == 10  # avg 5 × 2 = 10

    def test_criteria_reach_json_store(self, client):
        """Criteria dict flows from submit handler through to _write_feedback_json."""
        tc, _ = client

        import feedback.server as srv

        captured = {}

        def capture_json(job_id, url, title, org, score, action, comment,
                         tags=None, criteria=None):
            captured["criteria"] = criteria
            captured["score"] = score

        with patch.object(srv, "_write_feedback_json", side_effect=capture_json):
            with patch.object(srv, "_write_feedback_sqlite"):
                tc.post("/jobs/1/feedback", data={
                    "criteria_topic_fit": "4",
                    "criteria_methods_fit": "3",
                    "criteria_org_appeal": "4",
                    "criteria_career_fit": "5",
                    "criteria_location_fit": "4",
                })

        assert captured.get("criteria") == {
            "topic_fit": 4, "methods_fit": 3, "org_appeal": 4,
            "career_fit": 5, "location_fit": 4,
        }
        # avg(4,3,4,5,4) = 4.0; round(4.0 × 2) = 8
        assert captured.get("score") == 8

    def test_score_derived_from_criteria_average(self, client):
        """relevance_score = round(avg(criteria) × 2); all-3 → 6."""
        tc, _ = client

        import feedback.server as srv

        stored_score = {}

        def capture(job_id, relevance_score, tags, comment, criteria=None):
            stored_score["score"] = relevance_score

        with patch.object(srv, "_write_feedback_sqlite", side_effect=capture):
            tc.post("/jobs/1/feedback", data={
                "criteria_topic_fit": "3",
                "criteria_methods_fit": "3",
                "criteria_org_appeal": "3",
                "criteria_career_fit": "3",
                "criteria_location_fit": "3",
            })

        assert stored_score.get("score") == 6  # avg 3 × 2 = 6

    def test_criteria_values_clamped_to_1_5(self, client):
        """Values outside 1-5 are clamped before storage."""
        tc, _ = client

        import feedback.server as srv

        stored = {}

        def capture(job_id, relevance_score, tags, comment, criteria=None):
            stored["criteria"] = criteria

        with patch.object(srv, "_write_feedback_sqlite", side_effect=capture):
            tc.post("/jobs/1/feedback", data={
                "criteria_topic_fit": "0",    # below min → 1
                "criteria_methods_fit": "6",  # above max → 5
                "criteria_org_appeal": "3",
                "criteria_career_fit": "3",
                "criteria_location_fit": "3",
            })

        assert stored["criteria"]["topic_fit"] == 1
        assert stored["criteria"]["methods_fit"] == 5

    def test_applied_button_sets_action_applied(self, client):
        """POST with action_override=applied calls add_feedback with action='applied' and score=10."""
        tc, _ = client

        import feedback.server as srv

        captured = {}

        def capture_json(job_id, url, title, org, score, action, comment,
                         tags=None, criteria=None):
            captured["action"] = action
            captured["score"] = score

        with patch.object(srv, "_write_feedback_json", side_effect=capture_json):
            with patch.object(srv, "_write_feedback_sqlite"):
                tc.post("/jobs/1/feedback", data={
                    "action_override": "applied",
                })

        assert captured.get("action") == "applied"
        assert captured.get("score") == 10

    def test_job_not_found_returns_404(self, client):
        """POST to a non-existent job ID returns 404."""
        tc, _ = client
        resp = tc.post("/jobs/9999/feedback", data={})
        assert resp.status_code == 404

    def test_calibration_footer_shown_when_no_feedback(self, client):
        """GET /jobs shows the 'No feedback yet' calibration footer when total=0."""
        tc, _ = client
        resp = tc.get("/jobs")
        assert resp.status_code == 200
        assert b"No feedback yet" in resp.data or b"rate jobs" in resp.data.lower()

    def test_calibration_footer_shows_count_when_feedback_exists(self, tmp_path):
        """GET /jobs shows feedback count in the calibration footer when total > 0."""
        db_path = tmp_path / "test2.db"
        _make_test_db(db_path)

        import feedback.server as srv
        from flask import g

        def _mock_get_db():
            if "db" not in g:
                con = sqlite3.connect(str(db_path))
                con.row_factory = sqlite3.Row
                g.db = con
            return g.db

        summary = {"liked": [{"title": "x"}], "passed": [], "applied": [], "total": 1}
        with patch.object(srv, "_get_db", _mock_get_db):
            with patch("feedback.store.get_feedback_summary", return_value=summary):
                srv.app.config["TESTING"] = True
                tc = srv.app.test_client()
                resp = tc.get("/jobs")

        assert resp.status_code == 200
        assert b"Claude has learned from" in resp.data
        assert b"1 rating" in resp.data
