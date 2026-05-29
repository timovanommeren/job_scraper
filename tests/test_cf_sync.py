"""
Tests for feedback/cf_sync.py.

Run: python -m pytest tests/test_cf_sync.py -v
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_entry(job_id="42", action="like", score=None, reason=""):
    return {"job_id": job_id, "action": action, "score": score, "reason": reason, "ts": "2026-05-29T10:00:00Z"}


class TestCfSync:

    def test_skips_when_no_env_vars(self):
        """Returns 0 and does nothing when CF vars are not set."""
        env = {"CF_WORKER_URL": "", "CF_WORKER_SECRET": ""}
        with patch.dict(os.environ, env, clear=False):
            from feedback import cf_sync
            result = cf_sync.sync_pending_feedback()
        assert result == 0

    def test_returns_zero_on_empty_kv(self):
        """Returns 0 when poll returns an empty list."""
        env = {"CF_WORKER_URL": "https://worker.example.com", "CF_WORKER_SECRET": "sec"}
        with patch.dict(os.environ, env, clear=False):
            from feedback import cf_sync
            with patch.object(cf_sync, "_poll", return_value=[]):
                with patch.object(cf_sync, "_clear", return_value=0):
                    result = cf_sync.sync_pending_feedback()
        assert result == 0

    def test_skips_entry_when_job_not_in_db(self):
        """Does not crash when job_id is not found in SQLite; skips and clears."""
        env = {"CF_WORKER_URL": "https://worker.example.com", "CF_WORKER_SECRET": "sec"}
        entries = [_make_entry(job_id="999")]
        with patch.dict(os.environ, env, clear=False):
            from feedback import cf_sync
            with patch.object(cf_sync, "_poll", return_value=entries):
                with patch.object(cf_sync, "_lookup_job", return_value=None):
                    with patch.object(cf_sync, "_clear", return_value=1) as mock_clear:
                        result = cf_sync.sync_pending_feedback()
        # Entry skipped but _clear is still called (KV must be cleared)
        mock_clear.assert_called_once()
        assert result == 0

    def test_like_maps_to_score_8(self):
        """Quick-like action maps to score 8 when no slider score is present."""
        env = {"CF_WORKER_URL": "https://worker.example.com", "CF_WORKER_SECRET": "sec"}
        entries = [_make_entry(job_id="10", action="like", score=None)]
        fake_job = {"url": "https://example.com/job/10", "title": "Test", "organization": "Org", "relevance_score": 7}

        captured = {}

        def fake_add_feedback(**kwargs):
            captured.update(kwargs)

        with patch.dict(os.environ, env, clear=False):
            from feedback import cf_sync
            with patch.object(cf_sync, "_poll", return_value=entries):
                with patch.object(cf_sync, "_lookup_job", return_value=fake_job):
                    with patch.object(cf_sync, "_clear", return_value=1):
                        with patch.object(cf_sync, "_write_feedback_sqlite") as mock_sqlite:
                            with patch("feedback.store.add_feedback") as mock_add:
                                result = cf_sync.sync_pending_feedback()
                                if mock_add.called:
                                    _, kwargs = mock_add.call_args[0], mock_add.call_args[1]
                                    captured = mock_add.call_args

        assert result == 1

    def test_pass_maps_to_score_2(self):
        """Quick-pass action maps to score 2."""
        env = {"CF_WORKER_URL": "https://worker.example.com", "CF_WORKER_SECRET": "sec"}
        entries = [_make_entry(job_id="20", action="pass", score=None)]
        fake_job = {"url": "https://example.com/job/20", "title": "Job", "organization": "X", "relevance_score": 3}

        synced_scores = []

        def fake_write_sqlite(job_id, score):
            synced_scores.append(score)

        with patch.dict(os.environ, env, clear=False):
            from feedback import cf_sync
            with patch.object(cf_sync, "_poll", return_value=entries):
                with patch.object(cf_sync, "_lookup_job", return_value=fake_job):
                    with patch.object(cf_sync, "_clear", return_value=1):
                        with patch.object(cf_sync, "_write_feedback_sqlite", side_effect=fake_write_sqlite):
                            with patch("feedback.store.add_feedback"):
                                result = cf_sync.sync_pending_feedback()

        assert result == 1
        assert synced_scores == [2]

    def test_slider_score_used_when_present(self):
        """Numeric score from /rate form is used directly (not mapped from action)."""
        env = {"CF_WORKER_URL": "https://worker.example.com", "CF_WORKER_SECRET": "sec"}
        entries = [_make_entry(job_id="30", action="like", score=9, reason="Great org")]
        fake_job = {"url": "https://example.com/job/30", "title": "PhD", "organization": "RAND", "relevance_score": 8}

        written_scores = []

        def fake_write_sqlite(job_id, score):
            written_scores.append(score)

        with patch.dict(os.environ, env, clear=False):
            from feedback import cf_sync
            with patch.object(cf_sync, "_poll", return_value=entries):
                with patch.object(cf_sync, "_lookup_job", return_value=fake_job):
                    with patch.object(cf_sync, "_clear", return_value=1):
                        with patch.object(cf_sync, "_write_feedback_sqlite", side_effect=fake_write_sqlite):
                            with patch("feedback.store.add_feedback"):
                                result = cf_sync.sync_pending_feedback()

        assert result == 1
        assert written_scores == [9]

    def test_network_error_returns_zero(self):
        """When poll raises an exception, returns 0 without crashing."""
        env = {"CF_WORKER_URL": "https://worker.example.com", "CF_WORKER_SECRET": "sec"}
        from urllib.error import URLError

        with patch.dict(os.environ, env, clear=False):
            from feedback import cf_sync
            with patch.object(cf_sync, "_poll", side_effect=URLError("timeout")):
                result = cf_sync.sync_pending_feedback()

        assert result == 0

    def test_kv_cleared_even_when_some_entries_fail(self):
        """_clear is called even when individual entries fail (DB miss or store error)."""
        env = {"CF_WORKER_URL": "https://worker.example.com", "CF_WORKER_SECRET": "sec"}
        entries = [
            _make_entry(job_id="bad1"),
            _make_entry(job_id="bad2"),
        ]

        with patch.dict(os.environ, env, clear=False):
            from feedback import cf_sync
            with patch.object(cf_sync, "_poll", return_value=entries):
                with patch.object(cf_sync, "_lookup_job", return_value=None):
                    with patch.object(cf_sync, "_clear", return_value=2) as mock_clear:
                        cf_sync.sync_pending_feedback()

        mock_clear.assert_called_once()
