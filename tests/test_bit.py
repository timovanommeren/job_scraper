"""
Tests for the BIT (Behavioural Insights Team) scraper (scrapers/bit.py).

Uses mocked HTTP responses — no real network calls. The scraper makes one
listing GET plus one detail GET per card; the mock routes by URL.

Run: python -m pytest tests/test_bit.py -v
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

import scrapers.bit as bit_mod
from scrapers.bit import BITScraper

MINIMAL_SETTINGS = {"scraper": {"request_delay_seconds": 0}}

_CAREERS = "https://www.bi.team/about-us/careers/"


def _card(slug, title, office, deadline_text, excerpt):
    return f"""
    <article class="c-list-item">
      <div class="c-list-item__surtitle">
        <span class="c-list-item__surtitle-item c-list-item__surtitle-item--content-type">Vacancy</span>
        <span class="c-list-item__surtitle-item c-list-item__surtitle-item--office">{office}</span>
        <span class="c-list-item__surtitle-item c-list-item__surtitle-item--date">{deadline_text}</span>
      </div>
      <h3 class="c-list-item__title">{title}</h3>
      <a class="c-list-item__link" href="https://www.bi.team/vacancies/{slug}/">{title}</a>
      <p class="c-list-item__excerpt">{excerpt}</p>
    </article>
    """


def _listing(*cards):
    return "<html><body>" + "".join(cards) + "</body></html>"


def _detail(body):
    return f"<html><body><main>{body}</main></body></html>"


def _resp(text, *, raise_exc=None):
    r = MagicMock()
    r.text = text
    if raise_exc is not None:
        r.raise_for_status.side_effect = raise_exc
    else:
        r.raise_for_status.return_value = None
    return r


def _router(listing_html, detail_map, *, detail_exc_for=None):
    def _side_effect(url, **kwargs):
        if url == _CAREERS:
            return _resp(listing_html)
        if detail_exc_for and detail_exc_for in url:
            raise requests.RequestException("detail boom")
        return _resp(_detail(detail_map.get(url, "Default detail body.")))
    return _side_effect


class TestBITScraper:

    def test_parses_cards_with_metadata(self):
        listing = _listing(
            _card("senior-advisor-australia", "Senior Advisor - Australia", "Australia",
                  "Deadline for applications: 7th Jul 2026", "Join our Australian team."),
            _card("research-advisor-uk", "Research Advisor - UK", "London, UK",
                  "Deadline for applications: 1st Aug 2026", "Behavioural science research role."),
        )
        detail_map = {
            "https://www.bi.team/vacancies/research-advisor-uk/": "Full UK research description with RCTs.",
        }
        side = _router(listing, detail_map)
        with patch.object(bit_mod.requests, "get", side_effect=side):
            jobs = BITScraper(MINIMAL_SETTINGS).fetch()
        assert len(jobs) == 2
        uk = next(j for j in jobs if "UK" in j.title)
        assert uk.source == "bit"
        assert uk.organization == "Behavioural Insights Team"
        assert uk.location == "London, UK"
        assert uk.deadline == "1st Aug 2026"          # prefix stripped
        assert uk.url == "https://www.bi.team/vacancies/research-advisor-uk"  # canonicalized (trailing / dropped)
        assert "RCTs" in uk.raw_text                  # detail-page text merged in
        assert "Behavioural science research role" in uk.raw_text  # excerpt in header

    def test_detail_failure_falls_back_to_card_text(self):
        listing = _listing(
            _card("ops-canada", "Operations Coordinator - Canada", "Canada",
                  "Deadline for applications: 5th Jul 2026", "Ops role in Canada."),
        )
        side = _router(listing, {}, detail_exc_for="/vacancies/ops-canada/")
        with patch("time.sleep", return_value=None), \
             patch.object(bit_mod.requests, "get", side_effect=side):
            jobs = BITScraper(MINIMAL_SETTINGS).fetch()
        assert len(jobs) == 1
        j = jobs[0]
        assert j.title == "Operations Coordinator - Canada"
        assert "Ops role in Canada" in j.raw_text     # card excerpt fallback
        assert j.deadline == "5th Jul 2026"

    def test_missing_link_card_skipped(self):
        # A card with no link element must be skipped, not crash the scrape.
        broken = '<article class="c-list-item"><h3 class="c-list-item__title">No Link Role</h3></article>'
        listing = _listing(broken, _card("good", "Good Role", "London", "", "Real role."))
        side = _router(listing, {})
        with patch.object(bit_mod.requests, "get", side_effect=side):
            jobs = BITScraper(MINIMAL_SETTINGS).fetch()
        assert len(jobs) == 1
        assert jobs[0].title == "Good Role"

    def test_no_cards_returns_empty(self):
        side = _router("<html><body><p>No vacancies right now.</p></body></html>", {})
        with patch.object(bit_mod.requests, "get", side_effect=side):
            jobs = BITScraper(MINIMAL_SETTINGS).fetch()
        assert jobs == []

    def test_fetch_never_raises_when_listing_fails(self):
        def side(url, **kwargs):
            raise requests.RequestException("listing down")
        with patch("time.sleep", return_value=None), \
             patch.object(bit_mod.requests, "get", side_effect=side):
            jobs = BITScraper(MINIMAL_SETTINGS).fetch()
        assert jobs == []

    def test_raw_text_truncated_to_max(self):
        listing = _listing(_card("big", "Big Role", "London",
                                 "Deadline for applications: 9th Sep 2026", "short excerpt"))
        detail_map = {"https://www.bi.team/vacancies/big/": "X" * 9000}
        side = _router(listing, detail_map)
        with patch.object(bit_mod.requests, "get", side_effect=side):
            jobs = BITScraper(MINIMAL_SETTINGS).fetch()
        assert len(jobs[0].raw_text) <= 4000
