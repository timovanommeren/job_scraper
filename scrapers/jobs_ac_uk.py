# jobs.ac.uk scraper — UK academic & research jobs, keyword-filtered for drug policy.
# Portal: https://www.jobs.ac.uk/search/?keywords=<term>
#
# DESIGN NOTE (2026-06-23): the original plan was to consume jobs.ac.uk RSS feeds via
# feedparser. Probing showed jobs.ac.uk no longer exposes RSS/Atom feeds (all documented
# feed paths 404, no <link rel="alternate"> in page source, ?format=rss returns HTML).
# Pivoted to scraping the server-rendered search results page instead. This is strictly
# cleaner than the RSS plan: robots.txt allows /search/ (only /job/feedback/ and
# /enhanced/fp/ are disallowed), keyword filtering happens server-side via ?keywords=,
# and no new dependency (feedparser) is required.
#
# Coverage rationale: one keyword-filtered query against jobs.ac.uk covers all UK
# drug-policy research centres at once (Swansea GDPO, Essex Human Rights & Drug Policy,
# Bristol, KCL/National Addiction Centre, LSHTM). The keyword set is deliberately broad;
# a "drug" search also returns medicinal-chemistry roles, but the Layer 2 pre-screen and
# Claude scoring down-rank those — the keyword filter only narrows the firehose from "all
# UK academic jobs" to "drug-related", it is not the relevance gate.
#
# Verified 2026-06-23: search page server-rendered (requests + BS4). Cards are
#   div.j-search-result__result with a link a[href^='/job/']. Detail pages carry a rich
#   description (div[class*=advert]) plus "Closes" deadline and "Location:" — fetched per
#   job because cards alone are too thin for good scoring.

import json
import time
import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from .base import BaseScraper, RawJob

_BASE = "https://www.jobs.ac.uk"
_SEARCH = f"{_BASE}/search/"

# Deliberately broad — see module docstring. Each is a separate server-side query;
# results are deduplicated by job URL before detail fetch.
_KEYWORDS = [
    "drug policy",
    "addiction",
    "substance use",
    "harm reduction",
    "drug",
]


class JobsAcUkScraper(BaseScraper):
    source_name = "jobs_ac_uk"
    base_url = _SEARCH

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }

    def fetch(self) -> list:
        try:
            return self._scrape()
        except Exception:
            self.logger.exception("[jobs_ac_uk] Scraper failed")
            return []

    def _scrape(self) -> list:
        max_jobs = self.settings["scraper"]["max_jobs_per_site"]
        delay = self.settings["scraper"]["request_delay_seconds"]

        # 1) Collect candidate job paths across keyword queries, deduped by path.
        seen_paths: dict[str, str] = {}   # path -> card title (for logging)
        for kw in _KEYWORDS:
            if len(seen_paths) >= max_jobs:
                break
            try:
                soup = self._get_soup(_SEARCH, {"keywords": kw})
            except Exception:
                self.logger.warning(f"[jobs_ac_uk] keyword query failed: {kw!r}")
                continue
            cards = soup.select("div.j-search-result__result")
            for card in cards:
                link = card.select_one("a[href^='/job/']")
                if not link:
                    continue
                path = link.get("href", "").split("?")[0]
                if path and path not in seen_paths:
                    seen_paths[path] = link.get_text(strip=True)
            time.sleep(delay)

        self.logger.info(
            f"[jobs_ac_uk] {len(seen_paths)} unique candidate postings across "
            f"{len(_KEYWORDS)} keyword queries."
        )

        # 2) Fetch each detail page for rich raw_text + deadline.
        jobs = []
        for path, card_title in list(seen_paths.items())[:max_jobs]:
            url = _BASE + path
            try:
                detail = self._get_soup(url, None)
            except Exception:
                self.logger.warning(f"[jobs_ac_uk] detail fetch failed: {url}")
                continue
            job = self._parse_detail(detail, url, card_title)
            if job:
                jobs.append(job)
            time.sleep(delay)

        self.logger.info(f"[jobs_ac_uk] {len(jobs)} postings fetched.")
        return jobs

    def _parse_detail(self, soup: BeautifulSoup, url: str, fallback_title: str):
        # jobs.ac.uk embeds a schema.org JobPosting in ld+json on every detail page —
        # far more reliable than scraping divs (the HTML layout varies per advertiser).
        posting = self._jsonld_jobposting(soup)
        if posting:
            title = (posting.get("title") or fallback_title or "").strip()
            org = self._org_name(posting.get("hiringOrganization"))
            location = self._location_str(posting.get("jobLocation"))
            deadline = (posting.get("validThrough") or "").split("T")[0] or None
            desc_html = posting.get("description") or ""
            body = BeautifulSoup(desc_html, "lxml").get_text(" ", strip=True)
        else:
            # Fallback: no structured data — use the heading + main text.
            title_el = soup.select_one("h1")
            title = (title_el.get_text(strip=True) if title_el else fallback_title) or ""
            org = location = deadline = None
            content_el = soup.select_one("div[class*=advert]")
            body = (content_el or soup).get_text(" ", strip=True)

        if not title:
            return None

        return RawJob(
            title=title,
            url=self.canonicalize_url(url),
            source=self.source_name,
            raw_text=body[:4000],
            organization=org,
            location=location,
            deadline=deadline,
        )

    @staticmethod
    def _jsonld_jobposting(soup: BeautifulSoup):
        for block in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(block.string or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            for item in (data if isinstance(data, list) else [data]):
                if isinstance(item, dict) and item.get("@type") == "JobPosting":
                    return item
        return None

    @staticmethod
    def _org_name(hiring_org):
        if isinstance(hiring_org, dict):
            return hiring_org.get("name")
        return hiring_org or None

    @staticmethod
    def _location_str(job_location):
        """Build a 'Locality, Country' string from schema.org jobLocation (list or dict)."""
        places = job_location if isinstance(job_location, list) else [job_location]
        parts = []
        for p in places:
            if not isinstance(p, dict):
                continue
            addr = p.get("address") or {}
            if isinstance(addr, dict):
                bit = addr.get("addressLocality") or addr.get("addressRegion") or addr.get("addressCountry")
                if bit and bit not in parts:
                    parts.append(bit)
        return ", ".join(parts) if parts else None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
    def _get_soup(self, url: str, params) -> BeautifulSoup:
        resp = requests.get(url, params=params, headers=self.HEADERS, timeout=20)
        resp.raise_for_status()
        # Parse from raw bytes so BeautifulSoup honours the page's declared UTF-8
        # (preserves curly quotes / £ etc. in the JSON-LD description).
        return BeautifulSoup(resp.content, "lxml")
