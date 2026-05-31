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


class TestCriteriaStorage:

    def test_stores_entry_with_criteria(self, tmp_store):
        from feedback.store import add_feedback, get_all
        criteria = {"topic_fit": 4, "methods_fit": 5, "org_appeal": 3, "career_fit": 5, "location_fit": 4}
        add_feedback("10", "https://example.com/10", "Job X", "Org X", 8, "like",
                     criteria=criteria)
        items = get_all()
        assert items[0]["criteria"] == criteria

    def test_criteria_omitted_when_none(self, tmp_store):
        from feedback.store import add_feedback, get_all
        add_feedback("11", "https://example.com/11", "Job Y", "Org Y", 6, "pass")
        items = get_all()
        assert "criteria" not in items[0]

    def test_backward_compat_old_entry_has_no_criteria(self, tmp_store):
        """Old entries with only tags are readable alongside new criteria entries."""
        import json
        from feedback.store import add_feedback, get_all
        # Simulate a legacy entry by writing directly to the store
        import feedback.store as store_mod
        legacy = {
            "job_id": "99",
            "url": "https://example.com/99",
            "title": "Old Job",
            "organization": "Old Org",
            "score_given": 3,
            "action": "pass",
            "comment": "",
            "timestamp": "2026-05-01T00:00:00+00:00",
            "tags": ["Wrong field"],
        }
        data = {"items": [legacy]}
        store_mod._save(data)
        # Add a new entry with criteria
        add_feedback("100", "https://example.com/100", "New Job", "New Org", 8, "like",
                     criteria={"topic_fit": 4, "methods_fit": 5, "org_appeal": 4,
                                "career_fit": 5, "location_fit": 3})
        items = get_all()
        old = next(i for i in items if i["job_id"] == "99")
        new = next(i for i in items if i["job_id"] == "100")
        assert old.get("tags") == ["Wrong field"]
        assert "criteria" not in old
        assert new.get("criteria") is not None


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
