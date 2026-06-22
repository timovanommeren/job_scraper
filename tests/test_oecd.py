"""
Tests for the OECD SmartRecruiters scraper (scrapers/oecd.py).

Uses mocked HTTP responses — no real network calls. The scraper makes one
(paginated) list GET plus one detail GET per posting; the mock routes by URL.

Run: python -m pytest tests/test_oecd.py -v
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

import scrapers.oecd as oecd_mod
from scrapers.oecd import OecdScraper

MINIMAL_SETTINGS = {"scraper": {"request_delay_seconds": 0}}

_LIST_URL = "https://api.smartrecruiters.com/v1/companies/OECD/postings"


def _resp(json_data, *, raise_exc=None, json_exc=None):
    """Build a fake requests.Response."""
    r = MagicMock()
    if raise_exc is not None:
        r.raise_for_status.side_effect = raise_exc
    else:
        r.raise_for_status.return_value = None
    if json_exc is not None:
        r.json.side_effect = json_exc
    else:
        r.json.return_value = json_data
    return r


def _list_payload(ids, total=None):
    content = [{"id": i, "name": f"Job {i}", "location": {"fullLocation": "Paris, France"}} for i in ids]
    return {"totalFound": total if total is not None else len(content), "content": content}


def _detail_payload(pid, *, name=None, url=None, sections=None):
    return {
        "id": pid,
        "name": name if name is not None else f"Job {pid}",
        "postingUrl": url if url is not None else f"https://jobs.smartrecruiters.com/OECD/{pid}-job",
        "location": {"fullLocation": "Paris, Île-de-France Region, France"},
        "jobAd": {"sections": sections if sections is not None else {
            "jobDescription": {"text": "<p>Run quantitative policy analysis.</p>"},
            "qualifications": {"text": "<p>PhD in economics or statistics.</p>"},
        }},
    }


def _router(detail_map, *, list_payload=None, list_exc=None):
    """Return a side_effect that serves list vs detail responses by URL."""
    def _side_effect(url, **kwargs):
        if "/postings/" in url:  # detail endpoint: .../postings/{id}
            pid = url.rsplit("/", 1)[-1]
            entry = detail_map[pid]
            if isinstance(entry, dict) and entry.get("__json_exc__"):
                return _resp(None, json_exc=entry["__json_exc__"])
            return _resp(entry)
        # list endpoint
        if list_exc is not None:
            raise list_exc
        return _resp(list_payload)
    return _side_effect


class TestOecdScraper:

    def test_parses_list_and_detail_into_rawjobs(self):
        detail_map = {
            "100": _detail_payload("100", name="Senior Analyst"),
            "200": _detail_payload("200", name="Statistician"),
        }
        side = _router(detail_map, list_payload=_list_payload([100, 200]))
        with patch.object(oecd_mod.requests, "get", side_effect=side):
            jobs = OecdScraper(MINIMAL_SETTINGS).fetch()
        assert len(jobs) == 2
        titles = {j.title for j in jobs}
        assert titles == {"Senior Analyst", "Statistician"}
        j = jobs[0]
        assert j.source == "oecd"
        assert j.organization == "OECD"
        assert j.url.startswith("https://jobs.smartrecruiters.com/OECD/")
        assert "quantitative policy analysis" in j.raw_text  # HTML stripped

    def test_section_ordering_drops_company_boilerplate_on_truncation(self):
        # companyDescription is huge boilerplate; jobDescription is the real role.
        # Ordering must put the role first so [:4000] truncation drops boilerplate.
        sections = {
            "companyDescription": {"text": "<p>" + ("OECD ABOUT US " * 600) + "</p>"},  # ~8k chars
            "jobDescription": {"text": "<p>UNIQUE_ROLE_MARKER drug-policy research.</p>"},
            "qualifications": {"text": "<p>MSc methodology.</p>"},
        }
        detail_map = {"1": _detail_payload("1", sections=sections)}
        side = _router(detail_map, list_payload=_list_payload([1]))
        with patch.object(oecd_mod.requests, "get", side_effect=side):
            jobs = OecdScraper(MINIMAL_SETTINGS).fetch()
        assert len(jobs) == 1
        rt = jobs[0].raw_text
        assert len(rt) <= 4000
        assert "UNIQUE_ROLE_MARKER" in rt          # role survived
        assert "MSc methodology" in rt             # qualifications survived
        assert rt.index("UNIQUE_ROLE_MARKER") < rt.find("OECD ABOUT US")  # role before boilerplate

    def test_one_detail_failure_skips_only_that_posting(self):
        # Posting 200's detail returns a body whose .json() raises (malformed).
        # ValueError is not a RequestException, so no retry/sleep — fast.
        detail_map = {
            "100": _detail_payload("100"),
            "200": {"__json_exc__": ValueError("bad json")},
        }
        side = _router(detail_map, list_payload=_list_payload([100, 200]))
        with patch.object(oecd_mod.requests, "get", side_effect=side):
            jobs = OecdScraper(MINIMAL_SETTINGS).fetch()
        assert len(jobs) == 1
        assert jobs[0].title == "Job 100"

    def test_missing_posting_url_is_skipped(self):
        detail_map = {"1": _detail_payload("1", url="")}  # no postingUrl
        side = _router(detail_map, list_payload=_list_payload([1]))
        with patch.object(oecd_mod.requests, "get", side_effect=side):
            jobs = OecdScraper(MINIMAL_SETTINGS).fetch()
        assert jobs == []

    def test_empty_postings_returns_empty_list(self):
        side = _router({}, list_payload={"totalFound": 0, "content": []})
        with patch.object(oecd_mod.requests, "get", side_effect=side):
            jobs = OecdScraper(MINIMAL_SETTINGS).fetch()
        assert jobs == []

    def test_fetch_never_raises_when_list_endpoint_fails(self):
        # List GET always raises; retry exhausts, fetch() must swallow and return [].
        side = _router({}, list_exc=requests.RequestException("boom"))
        with patch("time.sleep", return_value=None), \
             patch.object(oecd_mod.requests, "get", side_effect=side):
            jobs = OecdScraper(MINIMAL_SETTINGS).fetch()
        assert jobs == []

    def test_pagination_follows_offset_across_pages(self):
        # totalFound 3 with a page limit of 2 → two list calls, 3 details fetched.
        list_pages = {
            0: {"totalFound": 3, "content": [
                {"id": "1", "name": "J1"}, {"id": "2", "name": "J2"}]},
            2: {"totalFound": 3, "content": [{"id": "3", "name": "J3"}]},
        }
        detail_map = {str(i): _detail_payload(str(i)) for i in (1, 2, 3)}

        def side(url, **kwargs):
            if "/postings/" in url:
                return _resp(detail_map[url.rsplit("/", 1)[-1]])
            offset = int(url.split("offset=")[1])
            return _resp(list_pages[offset])

        with patch.object(oecd_mod, "_PAGE_LIMIT", 2), \
             patch.object(oecd_mod.requests, "get", side_effect=side):
            jobs = OecdScraper(MINIMAL_SETTINGS).fetch()
        assert len(jobs) == 3
        # Title comes from the detail payload (name), not the list summary.
        assert {j.title for j in jobs} == {"Job 1", "Job 2", "Job 3"}
