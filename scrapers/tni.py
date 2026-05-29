import random
import time
import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .base import BaseScraper, RawJob

# Rate-limit note (verified 2026-05-28):
#   tni.org returns HTTP 429 for all automated requests regardless of User-Agent.
#   The block is IP/rate-based, not UA-based — header changes alone cannot bypass it.
#   Retry count was reduced from 5 → 2 to cap wasted time at ~10 s (was ~4.5 min).
#   If TNI ever becomes accessible again, re-verify the CSS selectors below.
#
# TODO: verify all CSS selectors below against the live tni.org jobs page DOM


class TNIScraper(BaseScraper):
    source_name = "tni"
    base_url = "https://www.tni.org/en/internships-jobs"

    # Use a realistic Chrome UA + language headers to avoid 429 bot detection.
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "nl-NL,nl;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    def fetch(self) -> list:
        try:
            return self._scrape_page()
        except Exception:
            self.logger.exception("TNI scraper failed")
            return []

    def _scrape_page(self) -> list:
        jobs = []
        soup = self._get_soup(self.base_url)
        # TODO: verify selector for TNI job items (small org, likely single page)
        cards = soup.select("div.view-content article, li.views-row, div.field-items div.field-item")
        for card in cards:
            # TODO: verify link selector for TNI
            link = card.select_one("h3 a, h2 a, .field-title a, span.field-content a")
            if not link:
                continue
            href = link.get("href", "")
            if href.startswith("/"):
                href = "https://www.tni.org" + href
            jobs.append(RawJob(
                title=link.get_text(strip=True),
                url=self.canonicalize_url(href),
                source=self.source_name,
                raw_text=card.get_text(separator=" ", strip=True)[:2000],
                organization="Transnational Institute (TNI)",
                location="Amsterdam, Netherlands",
            ))
        return jobs

    @retry(
        # 2 attempts max (1 retry) — saves ~2 min per run vs the old 5-attempt config
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=2, min=10, max=30),
        retry=retry_if_exception_type(requests.exceptions.RequestException),
    )
    def _get_soup(self, url: str) -> BeautifulSoup:
        # Random 2–5 s jitter makes the request pattern less bot-like
        time.sleep(random.uniform(2, 5))
        resp = requests.get(url, headers=self.HEADERS, timeout=15)
        if resp.status_code == 429:
            # Force tenacity to retry by raising as a retriable exception
            raise requests.exceptions.ConnectionError(f"429 Too Many Requests from {url}")
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")
