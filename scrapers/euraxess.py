import time
import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from .base import BaseScraper, RawJob


class EuraxessScraper(BaseScraper):
    source_name = "euraxess"
    base_url = "https://euraxess.ec.europa.eu/jobs/search"

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }

    def fetch(self) -> list:
        try:
            return self._scrape_listing_pages()
        except Exception:
            self.logger.exception("Euraxess scraper failed")
            return []

    def _scrape_listing_pages(self) -> list:
        jobs = []
        page = 0
        max_jobs = self.settings["scraper"]["max_jobs_per_site"]
        delay = self.settings["scraper"]["request_delay_seconds"]

        while len(jobs) < max_jobs:
            url = f"{self.base_url}?page={page}"
            soup = self._get_soup(url)
            # Confirmed selector: article.ecl-content-item (ECL design system)
            job_cards = soup.select("article.ecl-content-item")
            if not job_cards:
                break
            for card in job_cards:
                # Title link is inside h3 > a.ecl-link
                title_el = card.select_one("h3 a")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                href = title_el.get("href", "")
                if href.startswith("/"):
                    href = "https://euraxess.ec.europa.eu" + href
                # Organization: first <a> in the primary-meta list (institution link)
                org_el = card.select_one(".ecl-content-block__primary-meta-item a")
                # Location: meta items that are plain text (not links)
                meta_items = card.select(".ecl-content-block__primary-meta-item")
                loc = next(
                    (m.get_text(strip=True) for m in meta_items if not m.find("a")),
                    None
                )
                jobs.append(RawJob(
                    title=title,
                    url=self.canonicalize_url(href),
                    source=self.source_name,
                    raw_text=card.get_text(separator=" ", strip=True),
                    organization=org_el.get_text(strip=True) if org_el else None,
                    location=loc,
                ))
            page += 1
            time.sleep(delay)
        return jobs

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
    def _get_soup(self, url: str) -> BeautifulSoup:
        resp = requests.get(url, headers=self.HEADERS, timeout=20)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")
