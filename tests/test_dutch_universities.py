"""
Tests for T1: GenericStaticUniversityScraper and create_university_scrapers().

Uses mocked HTTP responses — no real network calls.
Run: python -m pytest tests/test_dutch_universities.py -v
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scrapers.dutch_universities import (
    UNIVERSITY_SCRAPER_CONFIGS,
    GenericStaticUniversityScraper,
    GenericPlaywrightUniversityScraper,
    create_university_scrapers,
)
from scrapers.base import RawJob


# ── Fixtures ───────────────────────────────────────────────────────────────────

MINIMAL_SETTINGS = {
    "scraper": {
        "request_delay_seconds": 0,
        "max_jobs_per_site": 50,
    }
}

UU_CONFIG = {
    "source_name": "uu",
    "org_name": "Utrecht University",
    "tech": "static",
    "list_url": "https://www.uu.nl/en/jobs",
    "filter_params": {},
    "location": "Utrecht, Nederland",
    "card_selector": "article",
    "title_sel": "h3",
    "link_sel": "a",
    "next_sel": "",
}

_LIST_HTML = """
<html><body>
  <article>
    <h3>PhD in Methodology</h3>
    <a href="/en/jobs/123">Apply</a>
  </article>
  <article>
    <h3>Researcher Social Science</h3>
    <a href="/en/jobs/456">Apply</a>
  </article>
</body></html>
"""

_DETAIL_HTML = """
<html><body>
  <main>Full job description text for testing purposes.</main>
</body></html>
"""


# ── create_university_scrapers() ───────────────────────────────────────────────

class TestCreateUniversityScrapers:

    def test_returns_all_seven_sources(self):
        registry = create_university_scrapers(MINIMAL_SETTINGS)
        expected = {"uu", "tilburg", "eur", "radboud", "uva", "vu", "rug"}
        assert set(registry.keys()) == expected

    def test_static_sources_are_correct_type(self):
        registry = create_university_scrapers(MINIMAL_SETTINGS)
        for name in ("uu", "tilburg", "eur", "radboud"):
            assert isinstance(registry[name], GenericStaticUniversityScraper), \
                f"{name} should be GenericStaticUniversityScraper"

    def test_playwright_sources_are_correct_type(self):
        registry = create_university_scrapers(MINIMAL_SETTINGS)
        for name in ("uva", "vu", "rug"):
            assert isinstance(registry[name], GenericPlaywrightUniversityScraper), \
                f"{name} should be GenericPlaywrightUniversityScraper"

    def test_source_names_match_config(self):
        registry = create_university_scrapers(MINIMAL_SETTINGS)
        for name, scraper in registry.items():
            assert scraper.source_name == name

    def test_config_list_has_seven_entries(self):
        assert len(UNIVERSITY_SCRAPER_CONFIGS) == 7

    def test_all_configs_have_required_keys(self):
        required = {"source_name", "org_name", "tech", "list_url", "location"}
        for cfg in UNIVERSITY_SCRAPER_CONFIGS:
            missing = required - set(cfg.keys())
            assert not missing, f"{cfg['source_name']} config missing: {missing}"


# ── GenericStaticUniversityScraper ─────────────────────────────────────────────

class TestGenericStaticUniversityScraper:

    def _make_scraper(self, cfg=None):
        return GenericStaticUniversityScraper(MINIMAL_SETTINGS, cfg or UU_CONFIG)

    @patch("scrapers.dutch_universities.requests.get")
    def test_returns_rawijobs_from_listing(self, mock_get):
        list_resp = MagicMock(status_code=200, text=_LIST_HTML)
        list_resp.raise_for_status = MagicMock()
        detail_resp = MagicMock(status_code=200, text=_DETAIL_HTML)
        detail_resp.raise_for_status = MagicMock()
        # First call = list page; subsequent = detail pages
        mock_get.side_effect = [list_resp, detail_resp, detail_resp]

        scraper = self._make_scraper()
        jobs = scraper.fetch()

        assert len(jobs) == 2
        assert all(isinstance(j, RawJob) for j in jobs)

    @patch("scrapers.dutch_universities.requests.get")
    def test_titles_extracted_correctly(self, mock_get):
        resp = MagicMock(status_code=200, text=_LIST_HTML)
        resp.raise_for_status = MagicMock()
        detail = MagicMock(status_code=200, text=_DETAIL_HTML)
        detail.raise_for_status = MagicMock()
        mock_get.side_effect = [resp, detail, detail]

        jobs = self._make_scraper().fetch()
        titles = {j.title for j in jobs}
        assert "PhD in Methodology" in titles
        assert "Researcher Social Science" in titles

    @patch("scrapers.dutch_universities.requests.get")
    def test_source_name_set_correctly(self, mock_get):
        resp = MagicMock(status_code=200, text=_LIST_HTML)
        resp.raise_for_status = MagicMock()
        detail = MagicMock(status_code=200, text=_DETAIL_HTML)
        detail.raise_for_status = MagicMock()
        mock_get.side_effect = [resp, detail, detail]

        jobs = self._make_scraper().fetch()
        assert all(j.source == "uu" for j in jobs)

    @patch("scrapers.dutch_universities.requests.get")
    def test_organization_set_from_config(self, mock_get):
        resp = MagicMock(status_code=200, text=_LIST_HTML)
        resp.raise_for_status = MagicMock()
        detail = MagicMock(status_code=200, text=_DETAIL_HTML)
        detail.raise_for_status = MagicMock()
        mock_get.side_effect = [resp, detail, detail]

        jobs = self._make_scraper().fetch()
        assert all(j.organization == "Utrecht University" for j in jobs)

    @patch("scrapers.dutch_universities.requests.get")
    def test_raw_text_capped_at_4000_chars(self, mock_get):
        long_detail = "<html><body><main>" + "x" * 10000 + "</main></body></html>"
        resp = MagicMock(status_code=200, text=_LIST_HTML)
        resp.raise_for_status = MagicMock()
        detail = MagicMock(status_code=200, text=long_detail)
        detail.raise_for_status = MagicMock()
        mock_get.side_effect = [resp, detail, detail]

        jobs = self._make_scraper().fetch()
        assert all(len(j.raw_text) <= 4000 for j in jobs)

    @patch("scrapers.dutch_universities.requests.get")
    def test_returns_empty_list_when_no_cards_found(self, mock_get):
        resp = MagicMock(status_code=200, text="<html><body><p>No jobs</p></body></html>")
        resp.raise_for_status = MagicMock()
        mock_get.return_value = resp

        jobs = self._make_scraper().fetch()
        assert jobs == []

    @patch("scrapers.dutch_universities.requests.get")
    def test_never_raises_on_http_error(self, mock_get):
        mock_get.side_effect = Exception("network failure")
        jobs = self._make_scraper().fetch()
        assert jobs == []

    @patch("scrapers.dutch_universities.requests.get")
    def test_detail_page_failure_falls_back_to_card_text(self, mock_get):
        """If the detail page fetch fails, raw_text falls back to card text."""
        list_resp = MagicMock(status_code=200, text=_LIST_HTML)
        list_resp.raise_for_status = MagicMock()
        # Detail pages raise
        detail_resp = MagicMock()
        detail_resp.raise_for_status.side_effect = Exception("404")
        mock_get.side_effect = [list_resp, detail_resp, detail_resp]

        jobs = self._make_scraper().fetch()
        # Should still return 2 jobs using card text as fallback
        assert len(jobs) == 2
        assert all(j.raw_text for j in jobs)

    @patch("scrapers.dutch_universities.requests.get")
    def test_content_type_defaults_to_job(self, mock_get):
        resp = MagicMock(status_code=200, text=_LIST_HTML)
        resp.raise_for_status = MagicMock()
        detail = MagicMock(status_code=200, text=_DETAIL_HTML)
        detail.raise_for_status = MagicMock()
        mock_get.side_effect = [resp, detail, detail]

        jobs = self._make_scraper().fetch()
        assert all(j.content_type == "job" for j in jobs)

    @patch("scrapers.dutch_universities.requests.get")
    def test_pagination_follows_next_link(self, mock_get):
        page1 = """
        <html><body>
          <article><h3>Job A</h3><a href="/jobs/1">link</a></article>
          <a rel='next' href='/en/jobs?page=2'>Next</a>
        </body></html>
        """
        page2 = """
        <html><body>
          <article><h3>Job B</h3><a href="/jobs/2">link</a></article>
        </body></html>
        """
        detail = MagicMock(status_code=200, text=_DETAIL_HTML)
        detail.raise_for_status = MagicMock()

        def side_effect(url, **kwargs):
            r = MagicMock(status_code=200)
            r.raise_for_status = MagicMock()
            r.text = page2 if "page=2" in url else page1
            return r if "jobs/1" not in url and "jobs/2" not in url else detail

        mock_get.side_effect = side_effect

        cfg = dict(UU_CONFIG, next_sel="a[rel='next']")
        jobs = GenericStaticUniversityScraper(MINIMAL_SETTINGS, cfg).fetch()
        titles = {j.title for j in jobs}
        assert "Job A" in titles
        assert "Job B" in titles
