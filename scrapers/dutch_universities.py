# Config-driven scrapers for 7 Dutch university careers portals.
#
# All selectors are PROVISIONAL — run the portal audit (The Assignment in the
# design doc) to verify each before trusting production results.
# Use: python main.py --site <source_name> --test
#
# Wave 1 (requests + BS4, static HTML): uu, tilburg, eur, radboud
# Wave 2 (Playwright, JS-rendered SPA): uva, vu, rug
#
# Silent zero check: if a scraper returns 0, source_yields in run_log records it.
# Also look for "0 cards found" WARNING in logs/scraper.log — this means selectors
# need updating, not that there are genuinely no open positions.

import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from .base import BaseScraper, PlaywrightBaseScraper, RawJob

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ── University portal configurations ──────────────────────────────────────────
#
# Config keys:
#   source_name     str   name registered in the scraper registry
#   org_name        str   organization name stored in DB / shown in email
#   tech            str   "static" (requests+BS4) | "playwright"
#   list_url        str   listing page URL
#   filter_params   dict  optional query params to pre-filter by job type
#   location        str   fallback location string when not found on page
#
#   -- static only --
#   card_selector   str   CSS selector for each job card on the listing page
#   title_sel       str   CSS selector for the title element within a card
#   link_sel        str   CSS selector for the detail-page <a> within a card
#   next_sel        str   CSS selector for the "next page" link ("" = no pagination)
#
#   -- playwright only --
#   wait_selector   str   selector passed to wait_for_selector before extracting
#   card_selector   str   CSS selector for each job card
#   title_sel       str   CSS selector for the title element within a card

UNIVERSITY_SCRAPER_CONFIGS: list[dict] = [
    # ── Wave 1: static HTML ───────────────────────────────────────────────────
    {
        # PROVISIONAL — verify selectors: python main.py --site uu --test
        # Portal: uu.nl runs Drupal; job cards are typically <article> or .views-row.
        # If 0 cards: open DevTools → Elements, find the repeating job card element,
        # copy its selector and update card_selector.
        "source_name": "uu",
        "org_name": "Utrecht University",
        "tech": "static",
        "list_url": "https://www.uu.nl/en/organisation/working-at-utrecht-university/jobs",
        "filter_params": {},
        "location": "Utrecht, Nederland",
        "card_selector": "article.views-row, li.views-row, .views-row, article",
        "title_sel": "h3, h2, .field--name-title, .views-field-title",
        "link_sel": "a",
        "next_sel": "a[rel='next'], .pager__item--next a, li.next a",
    },
    {
        # PROVISIONAL — verify selectors: python main.py --site tilburg --test
        # Portal: tilburguniversity.edu uses Drupal/custom CMS.
        "source_name": "tilburg",
        "org_name": "Tilburg University",
        "tech": "static",
        "list_url": "https://www.tilburguniversity.edu/about/working/vacancies",
        "filter_params": {},
        "location": "Tilburg, Nederland",
        "card_selector": ".vacancy, article.node--type-vacancy, .views-row, article",
        "title_sel": "h3, h2, .field--name-title",
        "link_sel": "a",
        "next_sel": "a[rel='next'], li.next a",
    },
    {
        # PROVISIONAL — verify selectors: python main.py --site eur --test
        # Portal: eur.nl custom CMS. Try the PhD-specific URL if the overview is paginated.
        "source_name": "eur",
        "org_name": "Erasmus University Rotterdam",
        "tech": "static",
        "list_url": "https://www.eur.nl/en/working-at-eur/vacancies/overview",
        "filter_params": {},
        "location": "Rotterdam, Nederland",
        "card_selector": ".vacancy-item, article, .views-row, li.vacancy",
        "title_sel": "h3, h2, .vacancy-title, .field--name-title",
        "link_sel": "a",
        "next_sel": "a[rel='next'], .pager-next a",
    },
    {
        # PROVISIONAL — verify selectors: python main.py --site radboud --test
        # Portal: ru.nl uses Pimcore-based CMS.
        "source_name": "radboud",
        "org_name": "Radboud University",
        "tech": "static",
        "list_url": "https://www.ru.nl/en/working-at/job-opportunities",
        "filter_params": {},
        "location": "Nijmegen, Nederland",
        "card_selector": ".vacancy, .job-item, article, li.views-row, .views-row",
        "title_sel": "h3, h2, .title, .field--name-title",
        "link_sel": "a",
        "next_sel": "a[rel='next'], .next-page a",
    },
    # ── Wave 2: Playwright (JS-rendered SPAs) ─────────────────────────────────
    {
        # PROVISIONAL — check DevTools Network tab for a JSON API first.
        # If werkenbij.uva.nl makes XHR requests to a /api/vacancies endpoint,
        # implement UvA as a requests+JSON scraper (like rand.py) instead.
        # Check: curl https://werkenbij.uva.nl/en/vacancies and look for data-* attrs.
        "source_name": "uva",
        "org_name": "University of Amsterdam",
        "tech": "playwright",
        "list_url": "https://werkenbij.uva.nl/en/vacancies",
        "filter_params": {},
        "location": "Amsterdam, Nederland",
        "wait_selector": "article, [class*='vacancy'], [class*='job-card'], [class*='job-item']",
        "card_selector": "article, [class*='vacancy-card'], [class*='job-item'], [class*='vacancy-item']",
        "title_sel": "h3, h2, [class*='title']",
    },
    {
        # PROVISIONAL — check if werkenbij.vu.nl uses the same ATS as UvA.
        # If yes, card_selector and title_sel will be identical.
        "source_name": "vu",
        "org_name": "Vrije Universiteit Amsterdam",
        "tech": "playwright",
        "list_url": "https://werkenbij.vu.nl/vacatures",
        "filter_params": {},
        "location": "Amsterdam, Nederland",
        "wait_selector": "article, [class*='vacancy'], [class*='job']",
        "card_selector": "article, [class*='vacancy'], [class*='job-item']",
        "title_sel": "h3, h2, [class*='title']",
    },
    {
        # PROVISIONAL — werkenbij.rug.nl is a separate career portal, likely SPA.
        "source_name": "rug",
        "org_name": "University of Groningen",
        "tech": "playwright",
        "list_url": "https://werkenbij.rug.nl/en/all-vacancies/",
        "filter_params": {},
        "location": "Groningen, Nederland",
        "wait_selector": "article, [class*='vacancy'], [class*='job']",
        "card_selector": "article, [class*='vacancy-item'], li[class*='job'], [class*='job-card']",
        "title_sel": "h3, h2, [class*='title']",
    },
]


# ── Generic static HTML scraper ────────────────────────────────────────────────

class GenericStaticUniversityScraper(BaseScraper):
    """
    Requests + BS4 scraper driven by a UNIVERSITY_SCRAPER_CONFIGS entry.

    Data flow:
      list_url → parse job cards → fetch each detail page → return RawJobs
      Follows pagination via next_sel. Applies request_delay between detail fetches.
    """

    def __init__(self, settings: dict, config: dict):
        super().__init__(settings)
        self.source_name = config["source_name"]
        self.base_url = config["list_url"]
        self._cfg = config

    def fetch(self) -> list:
        try:
            return self._scrape_all_pages()
        except Exception:
            self.logger.exception(f"[{self.source_name}] scraper failed")
            return []

    def _scrape_all_pages(self) -> list:
        jobs: list[RawJob] = []
        delay = self.settings["scraper"]["request_delay_seconds"]
        max_jobs = self.settings["scraper"]["max_jobs_per_site"]
        url: str | None = self.base_url
        first_page = True

        while url and len(jobs) < max_jobs:
            params = self._cfg.get("filter_params", {}) if first_page else {}
            soup = self._get_soup(url, params=params)
            page_jobs = self._extract_cards(soup)

            if not page_jobs:
                if first_page:
                    self.logger.warning(
                        f"[{self.source_name}] 0 job cards found on listing page. "
                        f"Selectors may need updating — tried: {self._cfg['card_selector']!r}. "
                        f"Run portal audit or check logs/scraper.log."
                    )
                break

            jobs.extend(page_jobs)
            first_page = False

            next_sel = self._cfg.get("next_sel", "")
            next_el = soup.select_one(next_sel) if next_sel else None
            if next_el and next_el.get("href"):
                href = next_el["href"]
                url = href if href.startswith("http") else urljoin(self.base_url, href)
                time.sleep(delay)
            else:
                break

        return jobs

    def _extract_cards(self, soup: BeautifulSoup) -> list[RawJob]:
        cards = soup.select(self._cfg["card_selector"])
        if not cards:
            return []

        jobs: list[RawJob] = []
        delay = self.settings["scraper"]["request_delay_seconds"]
        org = self._cfg["org_name"]
        location = self._cfg.get("location")

        for card in cards:
            title_el = card.select_one(self._cfg["title_sel"])
            link_el = card.select_one(self._cfg["link_sel"])
            if not title_el or not link_el:
                continue

            title = title_el.get_text(strip=True)
            href = link_el.get("href", "")
            if not href or not title:
                continue

            full_url = href if href.startswith("http") else urljoin(self.base_url, href)
            full_url = self.canonicalize_url(full_url)

            # Fetch detail page for full raw_text; fall back to card text on error.
            raw_text = card.get_text(separator=" ", strip=True)
            try:
                detail = self._get_soup(full_url)
                content = (
                    detail.select_one("main, article, [class*='job-detail'], [class*='vacancy-detail']")
                    or detail.body
                )
                if content:
                    raw_text = content.get_text(separator=" ", strip=True)
                time.sleep(delay)
            except Exception:
                self.logger.warning(f"[{self.source_name}] detail page fetch failed: {full_url!r}")

            jobs.append(RawJob(
                title=title,
                url=full_url,
                source=self.source_name,
                raw_text=raw_text[:4000],
                organization=org,
                location=location,
            ))

        return jobs

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
    def _get_soup(self, url: str, params: dict | None = None) -> BeautifulSoup:
        resp = requests.get(url, headers=_HEADERS, params=params or {}, timeout=20)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")


# ── Generic Playwright scraper ─────────────────────────────────────────────────

class GenericPlaywrightUniversityScraper(PlaywrightBaseScraper):
    """
    Playwright scraper driven by a UNIVERSITY_SCRAPER_CONFIGS entry.

    Uses list-page card text only (no detail-page fetches) to avoid the cost
    of N additional Playwright navigations. If full descriptions are needed,
    consider switching the portal to a static scraper after confirming detail
    pages are server-rendered.
    """

    def __init__(self, settings: dict, config: dict):
        super().__init__(settings)
        self.source_name = config["source_name"]
        self.base_url = config["list_url"]
        self._cfg = config

    async def _extract_jobs(self, page) -> list[RawJob]:
        wait_sel = self._cfg.get("wait_selector", "article")
        try:
            await page.wait_for_selector(wait_sel, timeout=10000)
        except Exception:
            self.logger.warning(
                f"[{self.source_name}] Timed out waiting for {wait_sel!r}. "
                f"0 vacancies returned — selector may need updating."
            )
            return []

        cards = await page.query_selector_all(self._cfg["card_selector"])
        if not cards:
            self.logger.warning(
                f"[{self.source_name}] 0 cards found with {self._cfg['card_selector']!r}"
            )
            return []

        jobs: list[RawJob] = []
        org = self._cfg["org_name"]
        location = self._cfg.get("location")
        seen: set[str] = set()

        for card in cards:
            # Title
            title_el = await card.query_selector(self._cfg["title_sel"])
            if not title_el:
                continue
            title = (await title_el.inner_text()).strip()
            if not title:
                continue

            # URL — try card's own href, then first <a> child
            href = await card.get_attribute("href") or ""
            if not href:
                a_el = await card.query_selector("a[href]")
                href = await a_el.get_attribute("href") if a_el else ""
            if not href or href in seen:
                continue
            seen.add(href)

            full_url = href if href.startswith("http") else urljoin(self.base_url, href)
            raw_text = (await card.inner_text()).strip()

            jobs.append(RawJob(
                title=title,
                url=self.canonicalize_url(full_url),
                source=self.source_name,
                raw_text=raw_text[:4000],
                organization=org,
                location=location,
            ))

        self.logger.info(f"[{self.source_name}] {len(jobs)} vacancies found")
        return jobs


# ── Factory ────────────────────────────────────────────────────────────────────

def create_university_scrapers(settings: dict) -> dict[str, BaseScraper]:
    """
    Instantiate all configured university scrapers.
    Returns {source_name: scraper_instance} — merge into build_scraper_registry().
    """
    scrapers: dict[str, BaseScraper] = {}
    for cfg in UNIVERSITY_SCRAPER_CONFIGS:
        if cfg["tech"] == "static":
            scrapers[cfg["source_name"]] = GenericStaticUniversityScraper(settings, cfg)
        elif cfg["tech"] == "playwright":
            scrapers[cfg["source_name"]] = GenericPlaywrightUniversityScraper(settings, cfg)
    return scrapers
