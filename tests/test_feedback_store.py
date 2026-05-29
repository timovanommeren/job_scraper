"""
Tests for feedback/store.py — tags field storage and backwards compat.

Run: python -m pytest tests/test_feedback_store.py -v
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture()
def tmp_store(tmp_path, monkeypatch):
    """Redirect STORE_PATH to a temp file for each test."""
    store_file = tmp_path / "feedback_store.json"
    import feedback.store as store_mod
    monkeypatch.setattr(store_mod, "STORE_PATH", store_file)
    return store_file


class TestAddFeedback:

    def test_stores_entry_without_tags(self, tmp_store):
        from feedback.store import add_feedback, get_all
        add_feedback("1", "https://example.com/1", "Job A", "Org A", 8, "like")
        items = get_all()
        assert len(items) == 1
        assert "tags" not in items[0]

    def test_stores_entry_with_tags(self, tmp_store):
        from feedback.store import add_feedback, get_all
        add_feedback("2", "https://example.com/2", "Job B", "Org B", 3, "pass",
                     tags=["Wrong field", "Too quantitative"])
        items = get_all()
        assert items[0]["tags"] == ["Wrong field", "Too quantitative"]

    def test_empty_tags_list_not_stored(self, tmp_store):
        from feedback.store import add_feedback, get_all
        add_feedback("3", "https://example.com/3", "Job C", "Org C", 5, "pass", tags=[])
        items = get_all()
        assert "tags" not in items[0]

    def test_upsert_replaces_existing_entry(self, tmp_store):
        from feedback.store import add_feedback, get_all
        add_feedback("4", "https://example.com/4", "Job D", "Org D", 8, "like")
        add_feedback("4", "https://example.com/4", "Job D", "Org D", 3, "pass",
                     tags=["Wrong field"])
        items = get_all()
        assert len(items) == 1
        assert items[0]["score_given"] == 3
        assert items[0]["tags"] == ["Wrong field"]

    def test_applied_action_stored(self, tmp_store):
        from feedback.store import add_feedback, get_all
        add_feedback("5", "https://example.com/5", "Job E", "Org E", 10, "applied")
        items = get_all()
        assert items[0]["action"] == "applied"


class TestGetFeedbackSummary:

    def test_summary_includes_applied(self, tmp_store):
        from feedback.store import add_feedback, get_feedback_summary
        add_feedback("1", "u1", "t1", "Org A", 8, "like")
        add_feedback("2", "u2", "t2", "Org B", 10, "applied")
        add_feedback("3", "u3", "t3", "Org C", 2, "pass")
        summary = get_feedback_summary()
        assert len(summary["liked"]) == 1
        assert len(summary["applied"]) == 1
        assert len(summary["passed"]) == 1
        assert summary["total"] == 3
