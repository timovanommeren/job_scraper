"""
Tests for structured tag form submit and feedback banner in feedback/server.py.

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
        mismatch_reasons TEXT, timestamp TEXT DEFAULT (datetime('now'))
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


class TestTagFormSubmit:

    def test_submit_redirects_to_feedback_saved(self, client):
        """POST /jobs/1/feedback returns 302 to /jobs?feedback=saved."""
        tc, _ = client
        resp = tc.post("/jobs/1/feedback", data={
            "relevance_score": "8",
            "tags": ["Great org", "Policy relevance"],
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

    def test_submit_without_tags_succeeds(self, client):
        """POST without any tags field still completes without error."""
        tc, _ = client
        resp = tc.post("/jobs/1/feedback", data={
            "relevance_score": "5",
            "comment": "OK job",
        })
        assert resp.status_code == 302

    def test_invalid_tags_are_filtered(self, client):
        """Tags not in ALLOWED_TAGS are dropped; valid tags are kept."""
        tc, db_path = client

        import feedback.server as srv

        captured_tags = {}

        def capture_sqlite(job_id, relevance_score, tags, comment):
            captured_tags["tags"] = tags

        with patch.object(srv, "_write_feedback_sqlite", side_effect=capture_sqlite):
            tc.post("/jobs/1/feedback", data={
                "relevance_score": "7",
                "tags": ["Wrong field", "INVALID_TAG_XYZ"],
            })

        assert captured_tags.get("tags") == ["Wrong field"]

    def test_tags_stored_via_write_feedback_sqlite(self, client):
        """Selected tags reach _write_feedback_sqlite as a list."""
        tc, _ = client

        import feedback.server as srv

        stored = {}

        def capture(job_id, relevance_score, tags, comment):
            stored["tags"] = tags

        with patch.object(srv, "_write_feedback_sqlite", side_effect=capture):
            tc.post("/jobs/1/feedback", data={
                "relevance_score": "9",
                "tags": ["Great org", "Interesting topic"],
            })

        assert set(stored.get("tags", [])) == {"Great org", "Interesting topic"}

    def test_applied_button_sets_action_applied(self, client):
        """POST with action_override=applied calls add_feedback with action='applied'."""
        tc, _ = client

        import feedback.server as srv

        captured_action = {}

        def capture_json(job_id, url, title, org, score, action, comment, tags=None):
            captured_action["action"] = action

        with patch.object(srv, "_write_feedback_json", side_effect=capture_json):
            with patch.object(srv, "_write_feedback_sqlite"):
                tc.post("/jobs/1/feedback", data={
                    "relevance_score": "10",
                    "action_override": "applied",
                })

        assert captured_action.get("action") == "applied"

    def test_job_not_found_returns_404(self, client):
        """POST to a non-existent job ID returns 404."""
        tc, _ = client
        resp = tc.post("/jobs/9999/feedback", data={"relevance_score": "5"})
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
