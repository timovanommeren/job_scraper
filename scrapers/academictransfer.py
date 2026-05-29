import time
import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from .base import BaseScraper, RawJob


class AcademicTransferScraper(BaseScraper):
    source_name = "academictransfer"
    # PhD and postdoc confirmed working. 'onderzoeker' category returns 404.
    TARGET_URLS = [
        "https://www.academictransfer.com/nl/functietype/phd/",
        "https://www.academictransfer.com/nl/functietype/postdoc/",
    ]
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
    }

    def fetch(self) -> list:
        jobs = []
        for base_url in self.TARGET_URLS:
            try:
                jobs.extend(self._scrape_paginated(base_url))
            except Exception:
                self.logger.exception(f"AcademicTransfer failed for {base_url}")
        return jobs

    def _scrape_paginated(self, base_url: str) -> list:
        jobs = []
        page = 1
        delay = self.settings["scraper"]["request_delay_seconds"]
        max_jobs = self.settings["scraper"]["max_jobs_per_site"]
        while len(jobs) < max_jobs:
            url = base_url if page == 1 else f"{base_url}?page={page}"
            soup = self._get_soup(url)
            # Site uses Nuxt/Vue with Tailwind: job cards are <article> elements
            cards = soup.find_all("article")
            if not cards:
                break
            for card in cards:
                # Title is in <h3>; href is on the overlay <a> (first anchor, no visible text)
                title_el = card.find("h3")
                link_el = card.find("a", href=True)
                if not title_el or not link_el:
                    continue
                title = title_el.get_text(strip=True)
                href = link_el.get("href", "")
                if href.startswith("/"):
                    href = "https://www.academictransfer.com" + href
                # Organization: last <span> text in right column (or img alt)
                org_img = card.find("img", alt=True)
                org = org_img["alt"] if org_img else None
                # Location: span that follows the location SVG icon (contains city text)
                loc_spans = card.find_all("span")
                loc = next(
                    (s.get_text(strip=True) for s in loc_spans
                     if s.get_text(strip=True) and s.find("svg") is None
                     and len(s.get_text(strip=True)) < 40
                     and s.get_text(strip=True) not in (org or "")),
                    None
                )
                jobs.append(RawJob(
                    title=title,
                    url=self.canonicalize_url(href),
                    source=self.source_name,
                    raw_text=card.get_text(separator=" ", strip=True)[:2000],
                    organization=org,
                    location=loc,
                ))
            # Pagination: look for a link containing page+1
            next_link = soup.find("a", attrs={"aria-label": lambda v: v and "next" in v.lower()}) \
                or soup.find("a", string=lambda t: t and str(page + 1) == t.strip())
            if not next_link:
                break
            page += 1
            time.sleep(delay)
        return jobs

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
    def _get_soup(self, url: str) -> BeautifulSoup:
        resp = requests.get(url, headers=self.HEADERS, timeout=15)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")
