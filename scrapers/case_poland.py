import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from .base import BaseScraper, RawJob


class CasePolandScraper(BaseScraper):
    source_name = "case_poland"
    base_url = "https://case-research.eu/career/"

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    }

    def fetch(self) -> list:
        try:
            return self._scrape_page()
        except Exception:
            self.logger.exception("CASE Poland scraper failed")
            return []

    def _scrape_page(self) -> list:
        jobs = []
        soup = self._get_soup(self.base_url)
        # Site uses Tailwind; each job card is an <article class="group/item">
        # The cover link (first <a>) points to the full job URL
        for card in soup.find_all("article"):
            link_el = card.find("a", href=True)
            title_el = card.find(["h2", "h3", "h4"])
            if not link_el or not title_el:
                continue
            href = link_el.get("href", "")
            if href.startswith("/"):
                href = "https://case-research.eu" + href
            jobs.append(RawJob(
                title=title_el.get_text(strip=True),
                url=self.canonicalize_url(href),
                source=self.source_name,
                raw_text=card.get_text(separator=" ", strip=True)[:2000],
                organization="CASE – Center for Social and Economic Research",
                location="Warsaw, Poland",
            ))
        return jobs

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
    def _get_soup(self, url: str) -> BeautifulSoup:
        resp = requests.get(url, headers=self.HEADERS, timeout=15)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")
