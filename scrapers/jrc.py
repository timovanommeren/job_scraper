import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from .base import BaseScraper, RawJob


class JRCScraper(BaseScraper):
    source_name = "jrc"
    # Single-page listing; JRC typically has very few open positions at any time
    base_url = "https://joint-research-centre.ec.europa.eu/students/current-phd-positions_en"

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    }

    def fetch(self) -> list:
        try:
            return self._scrape_page()
        except Exception:
            self.logger.exception("JRC scraper failed")
            return []

    def _scrape_page(self) -> list:
        jobs = []
        soup = self._get_soup(self.base_url)
        # Each vacancy is an h3 inside the article; sibling divs/paragraphs hold details
        article = soup.find("article")
        if not article:
            self.logger.warning("JRC: no <article> element found on page")
            return []
        for h3 in article.find_all("h3"):
            title = h3.get_text(strip=True)
            if not title:
                continue
            # Walk forward siblings to collect the vacancy block and find a link
            block_text_parts = [title]
            link_href = None
            for sib in h3.find_next_siblings():
                # Stop at next h3 (next vacancy)
                if sib.name == "h3":
                    break
                block_text_parts.append(sib.get_text(separator=" ", strip=True))
                if not link_href:
                    a = sib.find("a", href=True)
                    if a:
                        link_href = a["href"]
            if not link_href:
                # Fall back to the JRC page URL itself with a fragment
                link_href = self.base_url
            jobs.append(RawJob(
                title=title,
                url=self.canonicalize_url(link_href),
                source=self.source_name,
                raw_text=" ".join(block_text_parts)[:2000],
                organization="European Commission JRC",
                location="EU (Ispra / Seville / Brussels)",
            ))
        return jobs

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
    def _get_soup(self, url: str) -> BeautifulSoup:
        resp = requests.get(url, headers=self.HEADERS, timeout=15)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")
