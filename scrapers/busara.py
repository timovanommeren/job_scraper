# Busara uses Lever ATS — JSON API is faster and more reliable than scraping the React site.
# Verified 2026-05-28: https://api.lever.co/v0/postings/BusaraCenter?mode=json
# returns a JSON array of all open postings with full description text.

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .base import BaseScraper, RawJob

_LEVER_API = "https://api.lever.co/v0/postings/BusaraCenter"


class BusaraScraper(BaseScraper):
    source_name = "busara"
    base_url = _LEVER_API

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }

    def fetch(self) -> list:
        try:
            return self._fetch_all()
        except Exception:
            self.logger.exception("Busara scraper failed")
            return []

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=2, max=30),
        retry=retry_if_exception_type(requests.RequestException),
    )
    def _fetch_all(self) -> list:
        resp = requests.get(
            f"{_LEVER_API}?mode=json",
            headers=self.HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()  # list of job objects
        jobs = []
        for item in data:
            title = item.get("text", "")
            hosted_url = item.get("hostedUrl", "")
            if not title or not hosted_url:
                continue
            cats = item.get("categories", {})
            location = cats.get("location", "") or ", ".join(cats.get("allLocations", []))
            description = item.get("descriptionPlain", "") or item.get("descriptionBodyPlain", "")
            jobs.append(RawJob(
                title=title,
                url=self.canonicalize_url(hosted_url),
                source=self.source_name,
                raw_text=(title + "\n\n" + description)[:4000],
                organization="Busara Center for Behavioral Economics",
                location=location or "Nairobi, Kenya",
            ))
        if not jobs:
            self.logger.info("Busara: 0 open positions via Lever API")
        return jobs
