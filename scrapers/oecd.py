# OECD uses SmartRecruiters (careers.smartrecruiters.com/OECD/oecd---en).
# The public SmartRecruiters Posting API bypasses the Cloudflare-protected
# oecd.org HTML frontend entirely — no auth, no browser, plain requests.
#
# Verified 2026-06-22:
#   GET https://api.smartrecruiters.com/v1/companies/OECD/postings?limit=100
#       → {"totalFound": 22, "content": [ {id, name, location, ...}, ... ]}
#   GET https://api.smartrecruiters.com/v1/companies/OECD/postings/{id}
#       → {postingUrl, location, jobAd.sections: {jobDescription, qualifications,
#          additionalInformation, companyDescription}}
#
# Replaces the previous DISABLED stub. The old comment claimed OECD used Taleo;
# that was wrong — OECD migrated to SmartRecruiters, and the listings API is open.
#
# Data flow:
#   fetch()                                    BaseScraper contract: never raises, returns []
#     └─ _fetch_all()
#          ├─ _fetch_postings_list()           paginated list GET(s) — @retry
#          └─ for each posting:
#               try: _get_json(detail)         per-posting detail GET — @retry
#                    build raw_text            jobAd sections, HTML-stripped, role first
#               except: log + skip THIS one    one bad posting must not kill the scrape

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .base import BaseScraper, RawJob

_API_BASE = "https://api.smartrecruiters.com/v1/companies/OECD/postings"
_PAGE_LIMIT = 100
_RAW_TEXT_MAX = 4000

# jobAd sections in priority order. The actual role goes first and the ~6k-char
# OECD "who we are" boilerplate (companyDescription) goes last, so the [:4000]
# truncation drops boilerplate rather than the job description Claude scores on.
_SECTION_ORDER = ["jobDescription", "qualifications", "additionalInformation", "companyDescription"]


class OecdScraper(BaseScraper):
    source_name = "oecd"
    base_url = _API_BASE

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }

    def fetch(self) -> list:
        try:
            return self._fetch_all()
        except Exception:
            self.logger.exception("[OECD] scraper failed")
            return []

    def _fetch_all(self) -> list:
        postings = self._fetch_postings_list()
        if not postings:
            self.logger.info("[OECD] 0 open positions via SmartRecruiters API")
            return []
        jobs = []
        for posting in postings:
            job = self._build_job(posting)
            if job is not None:
                jobs.append(job)
        self.logger.info(f"[OECD] {len(jobs)} positions from {len(postings)} postings")
        return jobs

    def _fetch_postings_list(self) -> list:
        """Page through the list endpoint. Returns a list of posting summaries.

        Stops when a short page is returned or the running total reaches
        totalFound — defensive against OECD growing past one page (it has ~22).
        """
        collected: list = []
        offset = 0
        while True:
            data = self._get_json(f"{_API_BASE}?limit={_PAGE_LIMIT}&offset={offset}")
            content = data.get("content", []) or []
            collected.extend(content)
            total = data.get("totalFound", len(collected))
            offset += _PAGE_LIMIT
            if len(content) < _PAGE_LIMIT or len(collected) >= total:
                break
        return collected

    def _build_job(self, posting: dict):
        """Fetch one posting's detail and map to RawJob.

        Returns None (never raises) on failure or missing canonical URL, so one
        bad posting is skipped without aborting the whole scrape.
        """
        pid = posting.get("id")
        if not pid:
            return None
        try:
            detail = self._get_json(f"{_API_BASE}/{pid}")
        except Exception:
            self.logger.warning(f"[OECD] detail fetch failed for posting {pid}; skipping")
            return None

        url = detail.get("postingUrl")  # clean canonical URL; the dedup key
        title = detail.get("name") or posting.get("name") or ""
        if not url or not title:
            self.logger.warning(f"[OECD] posting {pid} missing url/title; skipping")
            return None

        loc = detail.get("location") or posting.get("location") or {}
        location = loc.get("fullLocation", "") if isinstance(loc, dict) else ""
        sections = (detail.get("jobAd") or {}).get("sections", {}) or {}
        raw_text = self._build_raw_text(title, sections)

        return RawJob(
            title=title,
            url=self.canonicalize_url(url),
            source=self.source_name,
            raw_text=raw_text,
            organization="OECD",
            location=location or "Paris, France",
        )

    @staticmethod
    def _build_raw_text(title: str, sections: dict) -> str:
        """Concatenate jobAd sections (role first), HTML-stripped, truncated."""
        parts = [title]
        for key in _SECTION_ORDER:
            sec = sections.get(key) or {}
            html = sec.get("text", "") if isinstance(sec, dict) else ""
            if html:
                text = BeautifulSoup(html, "lxml").get_text(separator=" ", strip=True)
                if text:
                    parts.append(text)
        return "\n\n".join(parts)[:_RAW_TEXT_MAX]

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=2, max=15),
        retry=retry_if_exception_type(requests.RequestException),
    )
    def _get_json(self, url: str) -> dict:
        resp = requests.get(url, headers=self.HEADERS, timeout=15)
        resp.raise_for_status()
        return resp.json()
