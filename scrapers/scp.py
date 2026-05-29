# The old scp.nl/over-het-scp/vacatures-bij-het-scp URL returns HTTP 404.
# Verified 2026-05-28: SCP no longer hosts a vacancy page on their own site.
# They post vacancies exclusively on werkenvoornederland.nl (Dutch gov jobs portal).
#
# The portal uses Bloomreach CMS and exposes a component-rendering endpoint
# that returns an HTML fragment without needing JS execution. This is the same
# pattern used by the WODC scraper.
#
# Verified 2026-05-28:
#   GET werkenvoornederland.nl/vacatures?_hn:type=component-rendering&_hn:ref=r48_r1_r4
#        &term=sociaal+cultureel+planbureau
#   Returns vacancy cards (1 result confirmed: "Senior juridisch adviseur, SCP").
#
# If the component ref (r48_r1_r4) ever stops working, open the page in browser dev
# tools, find <div id="vacancy-results-container" data-resource="..."> and copy the
# _hn:ref value.

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from .base import BaseScraper, RawJob

_BASE = "https://www.werkenvoornederland.nl"
_COMPONENT_REF = "r48_r1_r4"
_SEARCH_TERM = "sociaal cultureel planbureau"


class SCPScraper(BaseScraper):
    source_name = "scp"
    base_url = _BASE + "/vacatures"

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
    }

    def fetch(self) -> list:
        try:
            return self._scrape()
        except Exception:
            self.logger.exception("SCP scraper failed")
            return []

    def _scrape(self) -> list:
        soup = self._get_component_html(_SEARCH_TERM)
        cards = soup.find_all("li", class_=lambda c: c and "vacancy-list__item" in c)
        if not cards:
            self.logger.info("SCP: 0 vacancies currently listed on werkenvoornederland.nl")
            return []
        jobs = []
        for card in cards:
            title_el = card.select_one("h2.vacancy__title a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")
            if href.startswith("/"):
                href = _BASE + href
            employer_el = card.select_one("p.vacancy__employer")
            org = employer_el.get_text(strip=True) if employer_el else "Sociaal en Cultureel Planbureau (SCP)"
            jobs.append(RawJob(
                title=title,
                url=self.canonicalize_url(href),
                source=self.source_name,
                raw_text=card.get_text(separator=" ", strip=True)[:4000],
                organization=org,
                location="Den Haag, Nederland",
            ))
        self.logger.info(f"SCP: {len(jobs)} vacancies found")
        return jobs

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
    def _get_component_html(self, term: str) -> BeautifulSoup:
        resp = requests.get(
            self.base_url,
            params={
                "_hn:type": "component-rendering",
                "_hn:ref": _COMPONENT_REF,
                "term": term,
            },
            headers=self.HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")
