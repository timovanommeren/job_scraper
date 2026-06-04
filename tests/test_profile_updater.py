"""
Tests for feedback/profile_updater.py and agents/extractor_scorer.py profile rendering.

Run: python -m pytest tests/test_profile_updater.py -v
"""
import sys
import math
import yaml
from datetime import date
from pathlib import Path
from unittest.mock import patch

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
            from feedback import profile_updater
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
            from feedback import profile_updater
            result = profile_updater.generate_prompt_additions()

        assert "APPLIED" in result
        assert "SCP" in result

    def test_no_org_boost_in_generate_prompt_additions(self):
        """Org boost moved to _render_profile_prompt — must NOT appear here."""
        items = [_make_item("1", "RAND", 9, "like")]
        with patch("feedback.store.get_feedback_summary") as mock_summary:
            mock_summary.return_value = {
                "liked": items, "passed": [], "applied": [], "total": 1
            }
            from feedback import profile_updater
            result = profile_updater.generate_prompt_additions()

        assert "ORGANISATIONS WITH STRONG TRACK RECORD" not in result


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


# ── New tests for Phase 1 profile migration ────────────────────────────────────

_MINIMAL_PROFILE = {
    "qualifications": {
        "education": "MSc Test",
        "thesis": "Test thesis",
        "skills_technical": "Python, R",
        "experience": ["Internship at X", "Researcher at Y"],
        "languages": "Dutch, English",
        "location": "Amsterdam",
    },
    "hard_rules": {
        "disqualify": ["Postdoc / Postdoctoral — requires PhD"],
        "penalise_hard": ["Senior / Manager in title"],
    },
    "targets": {
        "sectors": ["EU institutions", "Think tanks"],
        "roles": ["PhD student position", "Researcher"],
        "bonus_topics": ["Drug policy", "Quantitative methods"],
    },
    "explicit_preferences": {
        "topic_fit":   {"weighted_mean": 7.5, "n_samples": 6, "last_updated": "2026-06-01"},
        "methods_fit": {"weighted_mean": 6.0, "n_samples": 6, "last_updated": "2026-06-01"},
    },
    "liked_organizations": ["RAND Corporation"],
}


class TestRenderProfilePrompt:

    def _render(self, profile=None):
        from agents.extractor_scorer import _render_profile_prompt
        return _render_profile_prompt(profile or _MINIMAL_PROFILE)

    def test_produces_nonempty_output(self):
        result = self._render()
        assert len(result) > 500

    def test_contains_required_sections(self):
        result = self._render()
        assert "HARD DISQUALIFIERS" in result
        assert "STRONG PENALTIES" in result
        assert "BONUS POINTS" in result

    def test_contains_postdoc_disqualifier(self):
        """Most critical disqualifier must survive the migration."""
        result = self._render()
        assert "Postdoc" in result

    def test_contains_phd_clarification(self):
        result = self._render()
        assert "PhD position" in result
        assert "APPLYING TO BECOME" in result

    def test_contains_scoring_scale(self):
        result = self._render()
        assert "Score 8" in result

    def test_liked_orgs_injected(self):
        result = self._render()
        assert "RAND Corporation" in result
        assert "STRONG TRACK RECORD" in result

    def test_explicit_preferences_injected_when_n_ge_5(self):
        result = self._render()
        assert "LEARNED PREFERENCE SIGNALS" in result
        assert "topic_fit" in result

    def test_explicit_preferences_omitted_when_n_lt_5(self):
        profile = dict(_MINIMAL_PROFILE)
        profile["explicit_preferences"] = {
            "topic_fit": {"weighted_mean": 7.0, "n_samples": 3, "last_updated": "2026-06-01"},
        }
        result = self._render(profile)
        assert "LEARNED PREFERENCE SIGNALS" not in result

    def test_missing_liked_orgs_key_no_error(self):
        profile = {k: v for k, v in _MINIMAL_PROFILE.items() if k != "liked_organizations"}
        result = self._render(profile)
        assert "HARD DISQUALIFIERS" in result

    def test_parity_with_old_prompt_content(self):
        """Rendered prompt must contain all substantive content areas from the old system prompt."""
        import yaml
        from pathlib import Path
        from agents.extractor_scorer import _render_profile_prompt
        profile_path = Path(__file__).parent.parent / "config" / "profile.yaml"
        profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        result = _render_profile_prompt(profile)
        for expected in [
            "HARD DISQUALIFIERS", "STRONG PENALTIES", "BONUS POINTS",
            "Postdoc", "PhD position", "RAND Corporation",
            "EMCDDA", "EUDA", "drug policy",
        ]:
            assert expected in result, f"Missing expected content: {expected!r}"


class TestRegressionModuleLevelConstant:

    def test_no_system_prompt_constant(self):
        """SYSTEM_PROMPT must not exist as a module-level constant after Phase 1."""
        import agents.extractor_scorer as m
        assert not hasattr(m, "SYSTEM_PROMPT"), "SYSTEM_PROMPT constant still present"

    def test_get_full_system_prompt_returns_nonempty(self, tmp_path):
        """_get_full_system_prompt() must return a non-empty string using new profile schema."""
        import yaml
        from pathlib import Path
        from agents import extractor_scorer

        profile_path = Path(__file__).parent.parent / "config" / "profile.yaml"
        profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))

        # Patch _PROFILE_PATH to point to a tmp copy of the real profile
        tmp_profile = tmp_path / "profile.yaml"
        tmp_profile.write_text(
            yaml.dump(profile, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )
        with patch.object(extractor_scorer, "_PROFILE_PATH", tmp_profile):
            result = extractor_scorer._get_full_system_prompt()

        assert len(result) > 500
        assert "HARD DISQUALIFIERS" in result

    def test_get_full_system_prompt_uses_fallback_when_profile_missing(self, tmp_path):
        """When profile.yaml is missing, fallback string is used (not empty)."""
        from agents import extractor_scorer
        missing = tmp_path / "nonexistent.yaml"
        with patch.object(extractor_scorer, "_PROFILE_PATH", missing):
            result = extractor_scorer._get_full_system_prompt()
        assert len(result) > 0
        assert "could not be loaded" in result.lower() or "Score all jobs" in result


class TestDecayWeight:

    def _call(self, age_days):
        from feedback.profile_updater import _decay_weight
        today = date(2026, 6, 4)
        past = date(today.year, today.month, today.day)
        from datetime import timedelta
        d = (past - timedelta(days=age_days)).isoformat()
        return _decay_weight(d, today)

    def test_age_zero_returns_one(self):
        assert abs(self._call(0) - 1.0) < 1e-9

    def test_age_half_life_returns_half(self):
        w = self._call(90)
        assert abs(w - 0.5) < 0.01

    def test_age_one_year_is_small(self):
        assert self._call(365) < 0.1

    def test_negative_age_clamped(self):
        from feedback.profile_updater import _decay_weight
        future = date(2026, 6, 10).isoformat()
        w = _decay_weight(future, date(2026, 6, 4))
        assert w == 1.0


class TestWeightedMean:

    def _call(self, scores_with_dates):
        from feedback.profile_updater import _weighted_mean
        return _weighted_mean(scores_with_dates, date(2026, 6, 4))

    def test_empty_returns_none(self):
        assert self._call([]) is None

    def test_single_item_returns_score(self):
        result = self._call([(7.0, "2026-06-04")])
        assert abs(result - 7.0) < 1e-6

    def test_all_same_age_returns_unweighted_mean(self):
        result = self._call([(6.0, "2026-06-04"), (8.0, "2026-06-04")])
        assert abs(result - 7.0) < 1e-6

    def test_newer_items_weighted_more(self):
        old_score = 2.0
        new_score = 8.0
        result = self._call([(old_score, "2025-06-04"), (new_score, "2026-06-04")])
        assert result > (old_score + new_score) / 2

    def test_result_bounded_between_1_and_10(self):
        result = self._call([(1.0, "2020-01-01"), (10.0, "2026-06-04")])
        assert 1.0 <= result <= 10.0


class TestUpdateExplicitPreferences:

    def _make_feedback_item(self, job_id, criteria, timestamp="2026-06-04T10:00:00Z"):
        return {
            "job_id": job_id, "title": "Test", "organization": "X",
            "score_given": 7, "action": "like", "comment": "",
            "timestamp": timestamp, "criteria": criteria,
        }

    def test_writes_explicit_preferences_to_profile(self, tmp_path):
        profile_file = tmp_path / "profile.yaml"
        profile_file.write_text("liked_organizations: []\n", encoding="utf-8")

        criteria = {"topic_fit": 8, "methods_fit": 7, "org_appeal": 6,
                    "career_fit": 8, "location_fit": 9}
        items = [self._make_feedback_item(str(i), criteria) for i in range(6)]

        import feedback.profile_updater as pu
        with patch("feedback.store.get_all", return_value=items):
            with patch.object(pu, "_PROFILE_PATH", profile_file):
                pu.update_explicit_preferences()

        profile = yaml.safe_load(profile_file.read_text(encoding="utf-8"))
        ep = profile["explicit_preferences"]
        assert "topic_fit" in ep
        assert ep["topic_fit"]["weighted_mean"] == 8.0
        assert ep["topic_fit"]["n_samples"] == 6

    def test_items_without_criteria_skipped(self, tmp_path):
        profile_file = tmp_path / "profile.yaml"
        profile_file.write_text("liked_organizations: []\n", encoding="utf-8")

        items = [
            {"job_id": "1", "title": "T", "organization": "X", "score_given": 7,
             "action": "like", "comment": "", "timestamp": "2026-06-04T10:00:00Z"},
        ]
        import feedback.profile_updater as pu
        with patch("feedback.store.get_all", return_value=items):
            with patch.object(pu, "_PROFILE_PATH", profile_file):
                pu.update_explicit_preferences()

        profile = yaml.safe_load(profile_file.read_text(encoding="utf-8"))
        assert "explicit_preferences" not in profile

    def test_no_items_does_not_write(self, tmp_path):
        profile_file = tmp_path / "profile.yaml"
        profile_file.write_text("liked_organizations: []\n", encoding="utf-8")
        original_mtime = profile_file.stat().st_mtime

        import feedback.profile_updater as pu
        with patch("feedback.store.get_all", return_value=[]):
            with patch.object(pu, "_PROFILE_PATH", profile_file):
                pu.update_explicit_preferences()

        assert profile_file.stat().st_mtime == original_mtime

    def test_atomic_write_creates_no_tmp_residue(self, tmp_path):
        profile_file = tmp_path / "profile.yaml"
        profile_file.write_text("liked_organizations: []\n", encoding="utf-8")

        criteria = {"topic_fit": 7, "methods_fit": 6, "org_appeal": 6,
                    "career_fit": 7, "location_fit": 8}
        items = [self._make_feedback_item(str(i), criteria) for i in range(6)]

        import feedback.profile_updater as pu
        with patch("feedback.store.get_all", return_value=items):
            with patch.object(pu, "_PROFILE_PATH", profile_file):
                pu.update_explicit_preferences()

        tmp_file = tmp_path / "profile.tmp"
        assert not tmp_file.exists(), ".tmp file should be cleaned up after atomic rename"
