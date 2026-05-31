"""
Tests for feedback/profile_updater.py — tags in prompt, org boost with threshold.

Run: python -m pytest tests/test_profile_updater.py -v
"""
import sys
import yaml
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_item(job_id, org, score, action="like", tags=None, comment=""):
    entry = {"job_id": job_id, "organization": org, "score_given": score,
             "action": action, "comment": comment, "title": "Test Job",
             "url": f"https://example.com/{job_id}"}
    if tags:
        entry["tags"] = tags
    return entry


class TestGeneratePromptAdditions:

    def test_includes_tags_in_few_shot_text(self):
        items = [
            _make_item("1", "RAND", 3, "pass", tags=["Wrong field", "Too quantitative"],
                       comment="Labour market"),
        ]
        with patch("feedback.store.get_feedback_summary") as mock_summary:
            mock_summary.return_value = {
                "liked": [], "passed": items, "applied": [], "total": 1
            }
            with patch("feedback.profile_updater._PROFILE_PATH") as mock_path:
                mock_path.read_text.return_value = "system_prompt: test\n"
                from feedback import profile_updater
                import importlib; importlib.reload(profile_updater)
                result = profile_updater.generate_prompt_additions()

        assert "Wrong field" in result
        assert "Too quantitative" in result
        assert "Labour market" in result

    def test_applied_action_boosted_to_top(self):
        items_applied = [_make_item("2", "SCP", 10, "applied")]
        items_liked   = [_make_item("3", "Busara", 8, "like")]
        with patch("feedback.store.get_feedback_summary") as mock_summary:
            mock_summary.return_value = {
                "liked": items_liked, "passed": [], "applied": items_applied, "total": 2
            }
            with patch("feedback.profile_updater._PROFILE_PATH") as mock_path:
                mock_path.read_text.return_value = "system_prompt: test\n"
                from feedback import profile_updater
                import importlib; importlib.reload(profile_updater)
                result = profile_updater.generate_prompt_additions()

        assert "APPLIED" in result
        assert "SCP" in result


class TestUpdateLikedOrganizations:
    # These tests patch _PROFILE_PATH via patch.object after importing the module.
    # Using importlib.reload() inside a patch context overwrites module-level globals
    # (including _PROFILE_PATH) so we avoid it here entirely.

    def test_org_not_added_with_single_signal(self, tmp_path):
        """Single rating ≥8 does NOT add org — requires 2+."""
        profile_file = tmp_path / "profile.yaml"
        profile_file.write_text("system_prompt: test\n", encoding="utf-8")

        items = [_make_item("1", "RAND Corporation", 8, "like")]
        import feedback.profile_updater as profile_updater
        with patch("feedback.store.get_all", return_value=items):
            with patch.object(profile_updater, "_PROFILE_PATH", profile_file):
                profile_updater.update_liked_organizations()

        profile = yaml.safe_load(profile_file.read_text(encoding="utf-8"))
        assert "liked_organizations" not in profile

    def test_org_added_with_two_signals(self, tmp_path):
        """Two ratings ≥8 from same org → added to profile.yaml."""
        profile_file = tmp_path / "profile.yaml"
        profile_file.write_text("system_prompt: test\n", encoding="utf-8")

        items = [
            _make_item("1", "RAND Corporation", 8, "like"),
            _make_item("2", "RAND Corporation", 9, "like"),
        ]
        import feedback.profile_updater as profile_updater
        with patch("feedback.store.get_all", return_value=items):
            with patch.object(profile_updater, "_PROFILE_PATH", profile_file):
                profile_updater.update_liked_organizations()

        profile = yaml.safe_load(profile_file.read_text(encoding="utf-8"))
        assert "RAND Corporation" in profile["liked_organizations"]

    def test_applied_counts_as_strong_signal(self, tmp_path):
        """action='applied' counts toward the 2+ threshold."""
        profile_file = tmp_path / "profile.yaml"
        profile_file.write_text("system_prompt: test\n", encoding="utf-8")

        items = [
            _make_item("1", "SCP", 8, "like"),
            _make_item("2", "SCP", 10, "applied"),
        ]
        import feedback.profile_updater as profile_updater
        with patch("feedback.store.get_all", return_value=items):
            with patch.object(profile_updater, "_PROFILE_PATH", profile_file):
                profile_updater.update_liked_organizations()

        profile = yaml.safe_load(profile_file.read_text(encoding="utf-8"))
        assert "SCP" in profile["liked_organizations"]

    def test_deduplication(self, tmp_path):
        """Same org with 3+ signals appears only once in the list."""
        profile_file = tmp_path / "profile.yaml"
        profile_file.write_text(
            "system_prompt: test\nliked_organizations:\n  - RAND Corporation\n",
            encoding="utf-8",
        )
        items = [
            _make_item("1", "RAND Corporation", 8, "like"),
            _make_item("2", "RAND Corporation", 9, "like"),
        ]
        import feedback.profile_updater as profile_updater
        with patch("feedback.store.get_all", return_value=items):
            with patch.object(profile_updater, "_PROFILE_PATH", profile_file):
                profile_updater.update_liked_organizations()

        profile = yaml.safe_load(profile_file.read_text(encoding="utf-8"))
        assert profile["liked_organizations"].count("RAND Corporation") == 1

    def test_capped_at_20(self, tmp_path):
        """Org list never exceeds 20 entries."""
        profile_file = tmp_path / "profile.yaml"
        profile_file.write_text("system_prompt: test\n", encoding="utf-8")

        # 25 orgs × 2 ratings each
        items = []
        for i in range(25):
            items.append(_make_item(f"{i}a", f"Org{i}", 8, "like"))
            items.append(_make_item(f"{i}b", f"Org{i}", 9, "like"))

        import feedback.profile_updater as profile_updater
        with patch("feedback.store.get_all", return_value=items):
            with patch.object(profile_updater, "_PROFILE_PATH", profile_file):
                profile_updater.update_liked_organizations()

        profile = yaml.safe_load(profile_file.read_text(encoding="utf-8"))
        assert len(profile["liked_organizations"]) == 20

    def test_score_below_8_not_counted(self, tmp_path):
        """Ratings below 8 are ignored for org boost even with 2+ entries."""
        profile_file = tmp_path / "profile.yaml"
        profile_file.write_text("system_prompt: test\n", encoding="utf-8")

        items = [
            _make_item("1", "BIT", 7, "like"),
            _make_item("2", "BIT", 6, "pass"),
        ]
        import feedback.profile_updater as profile_updater
        with patch("feedback.store.get_all", return_value=items):
            with patch.object(profile_updater, "_PROFILE_PATH", profile_file):
                profile_updater.update_liked_organizations()

        profile = yaml.safe_load(profile_file.read_text(encoding="utf-8"))
        assert "liked_organizations" not in profile


def _make_item_with_criteria(job_id, org, score, action="like", criteria=None, comment=""):
    entry = {"job_id": job_id, "organization": org, "score_given": score,
             "action": action, "comment": comment, "title": "Test Job",
             "url": f"https://example.com/{job_id}"}
    if criteria:
        entry["criteria"] = criteria
    return entry


class TestItemNote:

    def _call(self, item):
        from feedback.profile_updater import _item_note
        return _item_note(item)

    def test_formats_criteria_dict(self):
        """criteria dict → '[criteria: topic_fit:4, ...]' in annotation."""
        item = _make_item_with_criteria(
            "1", "RAND", 8,
            criteria={"topic_fit": 4, "methods_fit": 5, "org_appeal": 3,
                      "career_fit": 5, "location_fit": 4},
        )
        result = self._call(item)
        assert "[criteria:" in result
        assert "topic_fit:4" in result
        assert "methods_fit:5" in result

    def test_falls_back_to_tags_for_old_items(self):
        """Old items with tags but no criteria use the tag format."""
        item = _make_item("1", "RAND", 3, tags=["Wrong field", "Too quantitative"])
        result = self._call(item)
        assert "Wrong field" in result
        assert "[criteria:" not in result

    def test_includes_comment(self):
        """Comment appears in annotation for both criteria and tag items."""
        item = _make_item_with_criteria(
            "1", "RAND", 8, comment="Great policy relevance",
            criteria={"topic_fit": 4, "methods_fit": 4, "org_appeal": 5,
                      "career_fit": 4, "location_fit": 5},
        )
        result = self._call(item)
        assert "Great policy relevance" in result

    def test_empty_item_returns_empty_string(self):
        """Item with no criteria, tags, or comment → empty string."""
        item = {"job_id": "1", "organization": "X", "score_given": 5,
                "action": "pass", "comment": "", "title": "T",
                "url": "https://example.com/1"}
        result = self._call(item)
        assert result == ""
