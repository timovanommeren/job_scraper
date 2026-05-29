import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .base import BaseScraper, RawJob

# RAND uses Workday ATS; the public CXS JSON API is faster and more reliable than scraping HTML.
_WORKDAY_API = "https://rand.wd5.myworkdayjobs.com/wday/cxs/rand/External_Career_Site/jobs"
_JOB_BASE = "https://rand.wd5.myworkdayjobs.com/en-US/External_Career_Site"


class RandScraper(BaseScraper):
    source_name = "rand"
    base_url = _WORKDAY_API

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    def fetch(self) -> list:
        try:
            return self._fetch_all()
        except Exception:
            self.logger.exception("RAND scraper failed")
            return []

    def _fetch_all(self) -> list:
        jobs = []
        offset = 0
        limit = 20
        max_jobs = self.settings["scraper"].get("max_jobs_per_site", 100)

        while len(jobs) < max_jobs:
            data = self._fetch_page(offset, limit)
            postings = data.get("jobPostings", [])
            if not postings:
                break
            for p in postings:
                title = p.get("title", "")
                path = p.get("externalPath", "")
                url = _JOB_BASE + path if path else _WORKDAY_API
                location = p.get("locationsText", "") or p.get("primaryLocationText", "")
                snippet = p.get("jobDescription", "") or title
                jobs.append(RawJob(
                    title=title,
                    url=self.canonicalize_url(url),
                    source=self.source_name,
                    raw_text=snippet[:4000],
                    organization="RAND Corporation",
                    location=location,
                ))
            total = data.get("total", 0)
            offset += limit
            if offset >= total:
                break
        return jobs

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=2, max=30),
        retry=retry_if_exception_type(requests.RequestException),
    )
    def _fetch_page(self, offset: int, limit: int) -> dict:
        resp = requests.post(
            self.base_url,
            headers=self.HEADERS,
            json={"limit": limit, "offset": offset, "searchText": "", "appliedFacets": {}},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
