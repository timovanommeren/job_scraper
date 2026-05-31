import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse, urlunparse, urlencode, parse_qsl

logger = logging.getLogger(__name__)


@dataclass
class RawJob:
    title: str
    url: str            # canonical URL; primary dedup key
    source: str         # scraper name, e.g. "euraxess"
    raw_text: str       # full text of job posting (HTML-stripped)
    organization: Optional[str] = None
    location: Optional[str] = None
    deadline: Optional[str] = None  # raw string as found on page
    content_type: str = "job"       # "job" | "funding_call" | "conference"


class BaseScraper(ABC):
    source_name: str = ""   # must be overridden by subclass
    base_url: str = ""

    def __init__(self, settings: dict):
        self.settings = settings
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    def fetch(self) -> list:
        """
        Fetch all current job listings from this source.
        Must handle its own exceptions: catch scraping errors, log them,
        and return an empty list rather than propagating.
        Never returns None.
        """
        ...

    def canonicalize_url(self, url: str) -> str:
        """Strip tracking params, normalize scheme, lowercase host."""
        parsed = urlparse(url)
        clean_params = [
            (k, v) for k, v in parse_qsl(parsed.query)
            if not k.startswith(("utm_", "ref", "source", "campaign"))
        ]
        cleaned = parsed._replace(
            scheme=parsed.scheme.lower(),
            netloc=parsed.netloc.lower(),
            fragment="",
            query=urlencode(clean_params),
        )
        return urlunparse(cleaned).rstrip("/")


class PlaywrightBaseScraper(BaseScraper):
    """
    Base class for JS-rendered sites. Manages browser lifecycle.
    Subclasses must override _extract_jobs(page) and set source_name + base_url.
    """

    def fetch(self) -> list:
        try:
            return asyncio.run(self._async_fetch())
        except Exception:
            self.logger.exception(f"{self.source_name} Playwright fetch failed")
            return []

    async def _async_fetch(self) -> list:
        from playwright.async_api import async_playwright
        jobs = []
        # Use domcontentloaded + 15 s hard ceiling instead of networkidle (which
        # waits up to 30 s for ALL network activity to settle — unusable on SPAs
        # that poll indefinitely). Individual scrapers then wait for their specific
        # selector via wait_for_selector(timeout=10000).
        goto_timeout = 15000
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            page = await context.new_page()
            try:
                await page.goto(self.base_url, timeout=goto_timeout, wait_until="domcontentloaded")
                jobs = await self._extract_jobs(page)
            except Exception:
                self.logger.exception(f"{self.source_name} Playwright error during page load / extraction")
            finally:
                await browser.close()
        return jobs

    @abstractmethod
    async def _extract_jobs(self, page) -> list:
        """Extract RawJob objects from a fully-loaded Playwright page."""
        ...
