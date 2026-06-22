"""
Tests for the /settings route in feedback/server.py.

Run: python -m pytest tests/test_settings.py -v
"""
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from werkzeug.datastructures import MultiDict

sys.path.insert(0, str(Path(__file__).parent.parent))


_VALID_SETTINGS = {
    "scraper": {"request_delay_seconds": 2, "playwright_timeout_ms": 30000,
                "max_jobs_per_site": 100, "raw_text_max_chars": 4000},
    "filtering": {"strong_match_threshold": 6, "maybe_threshold": 5,
                  "email_also_min_score": 5},
    "email": {"send_if_no_new_jobs": True, "max_jobs_in_email": 50},
    "pre_filter": {"mode": "B"},
    "source_recommender": {"min_jobs": 5},
    "logging": {"level": "INFO"},
}


@pytest.fixture()
def client(tmp_path):
    """Flask test client with a temporary settings.yaml."""
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(yaml.safe_dump(_VALID_SETTINGS), encoding="utf-8")

    import feedback.server as srv

    with patch.object(srv, "_SETTINGS_PATH", settings_path):
        srv.app.config["TESTING"] = True
        yield srv.app.test_client(), settings_path


class TestSettingsGet:

    def test_get_returns_200(self, client):
        tc, _ = client
        resp = tc.get("/settings")
        assert resp.status_code == 200

    def test_get_pre_fills_values_from_yaml(self, client):
        tc, _ = client
        resp = tc.get("/settings")
        html = resp.data.decode()
        assert 'value="6"' in html   # strong_match_threshold
        assert 'value="5"' in html   # maybe_threshold / email_also_min_score
        assert 'value="50"' in html  # max_jobs_in_email

    def test_get_saved_banner_shown_with_param(self, client):
        tc, _ = client
        resp = tc.get("/settings?saved=1")
        assert b"Settings saved" in resp.data

    def test_get_no_saved_banner_without_param(self, client):
        tc, _ = client
        resp = tc.get("/settings")
        assert b"Settings saved" not in resp.data

    def test_get_corrupt_yaml_renders_error_page_not_500(self, tmp_path):
        settings_path = tmp_path / "bad.yaml"
        settings_path.write_text("filtering: [not: a: dict", encoding="utf-8")

        import feedback.server as srv
        with patch.object(srv, "_SETTINGS_PATH", settings_path):
            srv.app.config["TESTING"] = True
            tc = srv.app.test_client()
            resp = tc.get("/settings")

        assert resp.status_code == 200
        assert b"unreadable" in resp.data
        assert b"<form" not in resp.data  # no form rendered
        assert b"Save Settings" not in resp.data


class TestSettingsPost:

    def test_valid_post_redirects_to_saved(self, client):
        tc, _ = client
        resp = tc.post("/settings", data={
            "strong_match_threshold": "7",
            "maybe_threshold": "5",
            "email_also_min_score": "5",
            "send_if_no_new_jobs": "1",
            "max_jobs_in_email": "40",
            "source_recommender_min_jobs": "3",
        })
        assert resp.status_code == 302
        assert "saved=1" in resp.headers["Location"]

    def test_valid_post_updates_yaml_on_disk(self, client):
        tc, settings_path = client
        tc.post("/settings", data={
            "strong_match_threshold": "7",
            "maybe_threshold": "5",
            "email_also_min_score": "5",
            "send_if_no_new_jobs": "1",
            "max_jobs_in_email": "40",
            "source_recommender_min_jobs": "3",
        })
        written = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
        assert written["filtering"]["strong_match_threshold"] == 7
        assert written["email"]["max_jobs_in_email"] == 40

    def test_valid_post_preserves_non_exposed_keys(self, client):
        tc, settings_path = client
        tc.post("/settings", data={
            "strong_match_threshold": "6",
            "maybe_threshold": "5",
            "email_also_min_score": "5",
            "max_jobs_in_email": "50",
            "source_recommender_min_jobs": "5",
        })
        written = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
        # Non-exposed keys must survive the save
        assert written["scraper"]["playwright_timeout_ms"] == 30000
        assert written["logging"]["level"] == "INFO"
        assert written["pre_filter"]["mode"] == "B"

    def test_threshold_above_10_shows_error(self, client):
        tc, settings_path = client
        resp = tc.post("/settings", data={
            "strong_match_threshold": "11",
            "maybe_threshold": "5",
            "email_also_min_score": "5",
            "max_jobs_in_email": "50",
            "source_recommender_min_jobs": "5",
        })
        assert resp.status_code == 200
        assert b"must be 1" in resp.data
        # YAML unchanged
        original = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
        assert original["filtering"]["strong_match_threshold"] == 6

    def test_maybe_above_strong_shows_error(self, client):
        tc, _ = client
        resp = tc.post("/settings", data={
            "strong_match_threshold": "5",
            "maybe_threshold": "7",  # maybe > strong: invalid
            "email_also_min_score": "5",
            "max_jobs_in_email": "50",
            "source_recommender_min_jobs": "5",
        })
        assert resp.status_code == 200
        assert b"Maybe threshold" in resp.data

    def test_send_if_no_new_jobs_unchecked_stores_false(self, client):
        tc, settings_path = client
        # Unchecked: only the hidden field "0" is submitted (checkbox absent)
        tc.post("/settings", data=MultiDict([
            ("strong_match_threshold", "6"),
            ("maybe_threshold", "5"),
            ("email_also_min_score", "5"),
            ("send_if_no_new_jobs", "0"),  # hidden field only — checkbox not checked
            ("max_jobs_in_email", "50"),
            ("source_recommender_min_jobs", "5"),
        ]))
        written = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
        assert written["email"]["send_if_no_new_jobs"] is False

    def test_send_if_no_new_jobs_checked_stores_true(self, client):
        tc, settings_path = client
        # Checked: hidden field "0" + checkbox "1" both submitted
        tc.post("/settings", data=MultiDict([
            ("strong_match_threshold", "6"),
            ("maybe_threshold", "5"),
            ("email_also_min_score", "5"),
            ("send_if_no_new_jobs", "0"),  # hidden field
            ("send_if_no_new_jobs", "1"),  # checkbox checked
            ("max_jobs_in_email", "50"),
            ("source_recommender_min_jobs", "5"),
        ]))
        written = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
        assert written["email"]["send_if_no_new_jobs"] is True
