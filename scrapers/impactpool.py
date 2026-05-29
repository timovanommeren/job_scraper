# Impactpool — server-side rendered HTML; no Playwright needed.
# Verified 2026-05-28: job cards are <a href="/jobs/{id}"> anchor elements;
# title is in a child <div type="cardTitle">; organisation and location are
# in the first and second <div type="bodyEmphasis"> children respectively.
# Pagination exists but page 1 already returns 40 listings (max_jobs_per_site cap).

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from .base import BaseScraper, RawJob


class ImpactpoolScraper(BaseScraper):
    source_name = "impactpool"
    base_url = "https://www.impactpool.org/jobs"

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
    }

    def fetch(self) -> list:
        try:
            return self._scrape_page()
        except Exception:
            self.logger.exception("Impactpool scraper failed")
            return []

    def _scrape_page(self) -> list:
        jobs = []
        max_jobs = self.settings["scraper"]["max_jobs_per_site"]
        soup = self._get_soup(self.base_url)
        # Each card is an <a> whose href starts with /jobs/{numeric id}
        cards = soup.find_all("a", href=lambda h: h and h.startswith("/jobs/"))
        for card in cards[:max_jobs]:
            href = card.get("href", "")
            url = "https://www.impactpool.org" + href
            title_el = card.find(attrs={"type": "cardTitle"})
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            body_els = card.find_all(attrs={"type": "bodyEmphasis"})
            org = body_els[0].get_text(strip=True) if body_els else None
            loc = body_els[1].get_text(strip=True) if len(body_els) > 1 else None
            jobs.append(RawJob(
                title=title,
                url=self.canonicalize_url(url),
                source=self.source_name,
                raw_text=card.get_text(separator=" ", strip=True)[:4000],
                organization=org,
                location=loc,
            ))
        self.logger.info(f"Impactpool: scraped {len(jobs)} jobs")
        return jobs

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
    def _get_soup(self, url: str) -> BeautifulSoup:
        resp = requests.get(url, headers=self.HEADERS, timeout=15)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")
