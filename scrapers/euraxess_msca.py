# EURAXESS MSCA Doctoral Network scraper.
# Portal: https://euraxess.ec.europa.eu/jobs/search
#
# This is a SEPARATE scraper from euraxess.py (which is on the NEVER-MODIFY list).
# It runs the same EURAXESS faceted search but filtered to:
#   - Funding programme = "Horizon Europe - MSCA"  (job_is_eu_founded[]=4348)
#   - Researcher profile = "First Stage Researcher (R1)"  (job_research_profile[]=447)
#
# Why a separate scraper: euraxess.py runs unfiltered and already scrapes all R1 jobs,
# but it stops at max_jobs_per_site (sorted by date) so MSCA positions buried deeper get
# dropped. This filtered pass surfaces MSCA joint/industrial doctorates specifically and
# tags them so Claude prioritises them as the structural twin of a Collaborative Doctoral
# Programme. Roles also caught by euraxess.py collide on the L2 content_hash (title+org)
# and are skipped by dedup — the sentinel below rides in raw_text, which does not affect
# content_hash.
#
# Verified 2026-06-23: facet param names confirmed from the live search form
#   (job_is_eu_founded[]=4348 = "Horizon Europe - MSCA", job_research_profile[]=447 =
#   "First Stage Researcher (R1)"). Server-rendered HTML, requests + BS4 sufficient.
#   Card selector article.ecl-content-item (ECL design system), same as euraxess.py.

import time
import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from .base import BaseScraper, RawJob

_SENTINEL = "[SOURCE NOTE] MSCA Doctoral Network — joint/industrial doctorate.\n\n"


class EuraxessMscaScraper(BaseScraper):
    source_name = "euraxess_msca"
    base_url = "https://euraxess.ec.europa.eu/jobs/search"

    # Filter facets confirmed from the live search form (2026-06-23).
    FILTER_PARAMS = {
        "job_is_eu_founded[]": "4348",      # Horizon Europe - MSCA
        "job_research_profile[]": "447",     # First Stage Researcher (R1)
    }

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }

    def fetch(self) -> list:
        try:
            return self._scrape_listing_pages()
        except Exception:
            self.logger.exception("[euraxess_msca] Scraper failed")
            return []

    def _scrape_listing_pages(self) -> list:
        jobs = []
        page = 0
        max_jobs = self.settings["scraper"]["max_jobs_per_site"]
        delay = self.settings["scraper"]["request_delay_seconds"]

        while len(jobs) < max_jobs:
            params = dict(self.FILTER_PARAMS)
            params["page"] = page
            soup = self._get_soup(self.base_url, params)
            job_cards = soup.select("article.ecl-content-item")
            if not job_cards:
                break
            for card in job_cards:
                title_el = card.select_one("h3 a")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                href = title_el.get("href", "")
                if href.startswith("/"):
                    href = "https://euraxess.ec.europa.eu" + href
                org_el = card.select_one(".ecl-content-block__primary-meta-item a")
                meta_items = card.select(".ecl-content-block__primary-meta-item")
                # Plain-text (non-link) meta items; skip the "Posted on: ..." date,
                # which is the only non-link primary meta on MSCA cards (no country field).
                loc = next(
                    (
                        m.get_text(strip=True)
                        for m in meta_items
                        if not m.find("a")
                        and not m.get_text(strip=True).lower().startswith("posted on")
                    ),
                    None,
                )
                raw_text = _SENTINEL + card.get_text(separator=" ", strip=True)
                jobs.append(RawJob(
                    title=title,
                    url=self.canonicalize_url(href),
                    source=self.source_name,
                    raw_text=raw_text[:4000],
                    organization=org_el.get_text(strip=True) if org_el else None,
                    location=loc,
                ))
            page += 1
            time.sleep(delay)

        self.logger.info(f"[euraxess_msca] {len(jobs)} MSCA R1 positions fetched.")
        return jobs

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
    def _get_soup(self, url: str, params: dict) -> BeautifulSoup:
        resp = requests.get(url, params=params, headers=self.HEADERS, timeout=20)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")
