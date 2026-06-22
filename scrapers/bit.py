# Behavioural Insights Team (BIT) — WordPress careers page (requests + BS4).
#
# Verified 2026-06-22: https://www.bi.team/about-us/careers/ returns 200 to a
# plain request with a normal User-Agent. The Cloudflare block that previously
# disabled this scraper (#4) no longer fires on the careers path. The WordPress
# REST API for the custom post type is closed (wp-json/wp/v2/vacancies → 404
# rest_no_route), so this parses the listing HTML directly.
#
# Listing structure (confirmed 2026-06-22):
#   article.c-list-item
#     div.c-list-item__surtitle
#       span.c-list-item__surtitle-item--office   → location ("Australia")
#       span.c-list-item__surtitle-item--date     → "Deadline for applications: 7th Jul 2026"
#     h3.c-list-item__title                        → title
#     a.c-list-item__link[href]                    → /vacancies/{slug}/
#     p.c-list-item__excerpt                       → short description
#   Detail page <main> holds the full vacancy text.
#
# Note: BIT posts globally (AU/Canada/UK/...). No geo filtering here — Layer 2
# pre-screen handles relevance, as with Tilburg's Dutch admin roles.

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .base import BaseScraper, RawJob

_CAREERS_URL = "https://www.bi.team/about-us/careers/"
_RAW_TEXT_MAX = 4000
_DEADLINE_PREFIX = "Deadline for applications:"


class BITScraper(BaseScraper):
    source_name = "bit"
    base_url = _CAREERS_URL

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml",
    }

    def fetch(self) -> list:
        try:
            return self._fetch_all()
        except Exception:
            self.logger.exception("[BIT] scraper failed")
            return []

    def _fetch_all(self) -> list:
        soup = self._get_soup(_CAREERS_URL)
        cards = soup.select("article.c-list-item")
        if not cards:
            self.logger.info("[BIT] 0 vacancies found on careers page")
            return []
        jobs = []
        for card in cards:
            job = self._build_job(card)
            if job is not None:
                jobs.append(job)
        self.logger.info(f"[BIT] {len(jobs)} vacancies from {len(cards)} cards")
        return jobs

    def _build_job(self, card):
        """Map one listing card to a RawJob. Returns None (never raises) on a
        missing link/title, so one bad card doesn't abort the scrape."""
        link_el = card.select_one("a.c-list-item__link[href]")
        title_el = card.select_one("h3.c-list-item__title")
        if not link_el or not title_el:
            return None
        url = link_el.get("href", "").strip()
        title = title_el.get_text(strip=True)
        if not url or not title:
            return None

        office_el = card.select_one(".c-list-item__surtitle-item--office")
        date_el = card.select_one(".c-list-item__surtitle-item--date")
        excerpt_el = card.select_one(".c-list-item__excerpt")
        location = office_el.get_text(strip=True) if office_el else None
        excerpt = excerpt_el.get_text(" ", strip=True) if excerpt_el else ""
        deadline = None
        if date_el:
            deadline = date_el.get_text(strip=True).replace(_DEADLINE_PREFIX, "").strip() or None

        # Card-level header always present; enrich with full detail-page text when
        # the detail fetch succeeds, otherwise fall back to the card excerpt so a
        # broken detail page still yields a scoreable posting.
        header = "\n".join(p for p in [title, location, excerpt] if p)
        detail_text = self._fetch_detail_text(url)
        raw_text = (header + ("\n\n" + detail_text if detail_text else ""))[:_RAW_TEXT_MAX]

        return RawJob(
            title=title,
            url=self.canonicalize_url(url),
            source=self.source_name,
            raw_text=raw_text,
            organization="Behavioural Insights Team",
            location=location,
            deadline=deadline,
        )

    def _fetch_detail_text(self, url: str) -> str:
        """Full vacancy text from the detail page <main>. Returns '' on failure."""
        try:
            soup = self._get_soup(url)
            main = soup.select_one("main")
            return main.get_text(" ", strip=True) if main else ""
        except Exception:
            self.logger.warning(f"[BIT] detail fetch failed for {url}; using card text only")
            return ""

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=2, max=15),
        retry=retry_if_exception_type(requests.RequestException),
    )
    def _get_soup(self, url: str) -> BeautifulSoup:
        resp = requests.get(url, headers=self.HEADERS, timeout=20)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")
