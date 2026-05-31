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
        # Verified 2026-05-31: uu.nl Drupal CMS. 24 li.overview-list__item found.
        # Title and href both live on <a class="list-item__title"> inside each <li>.
        "source_name": "uu",
        "org_name": "Utrecht University",
        "tech": "static",
        "list_url": "https://www.uu.nl/en/organisation/working-at-utrecht-university/jobs",
        "filter_params": {},
        "location": "Utrecht, Nederland",
        "card_selector": "li.overview-list__item",
        "title_sel": "a.list-item__title",
        "link_sel": "a.list-item__title",
        "next_sel": "a[rel='next'], .pager__item--next a",
    },
    {
        # Verified 2026-05-31: Tilburg uses SAP SuccessFactors (fully JS-rendered).
        # Requires clicking "Search Jobs" (pre_click) to load results.
        # Job title links are <a href="...career_job_req_id=NNNN..."> — card IS the <a>,
        # so title_sel is "" (use card's own text) and href comes from card.get_attribute("href").
        "source_name": "tilburg",
        "org_name": "Tilburg University",
        "tech": "playwright",
        "list_url": "https://career5.successfactors.eu/career?company=S003974031P&lang=en_US",
        "filter_params": {},
        "location": "Tilburg, Nederland",
        "pre_click": "input[type='submit'][value*='Search'], button:has-text('Search Jobs')",
        "wait_selector": "a[href*='career_job_req_id']",
        "card_selector": "a[href*='career_job_req_id']",
        "title_sel": "",
    },
    {
        # Verified 2026-05-31: eur.nl custom CMS. 10 div.teaser per page + pagination.
        # Title and href both live on <a class="teaser__link"> inside each div.teaser.
        # Pagination: li.pager__item--next a → ?page=N (relative, resolved via urljoin).
        "source_name": "eur",
        "org_name": "Erasmus University Rotterdam",
        "tech": "static",
        "list_url": "https://www.eur.nl/en/working-at-eur/vacancies/overview",
        "filter_params": {},
        "location": "Rotterdam, Nederland",
        "card_selector": "div.teaser",
        "title_sel": "a.teaser__link",
        "link_sel": "a.teaser__link",
        "next_sel": "li.pager__item--next a",
    },
    {
        # Verified 2026-05-31: ru.nl Drupal CMS. 13 div.node--type-vacancy found.
        # Title text from span.link__text; href from h2.card__title > a.
        # No next-page link visible (all results on one page at audit time).
        "source_name": "radboud",
        "org_name": "Radboud University",
        "tech": "static",
        "list_url": "https://www.ru.nl/en/working-at/job-opportunities",
        "filter_params": {},
        "location": "Nijmegen, Nederland",
        "card_selector": "div.node--type-vacancy",
        "title_sel": "span.link__text",
        "link_sel": "h2.card__title a",
        "next_sel": "a[rel='next'], .pager__item--next a",
    },
    # ── Wave 2: Playwright (JS-rendered SPAs) ─────────────────────────────────
    {
        # Verified 2026-05-31 via static fetch: werkenbij.uva.nl returns content
        # via requests (not JS-gated). Keeping as Playwright for safety since the
        # site structure wasn't fully confirmed. Selectors provisional.
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
        # Verified 2026-05-31: werkenbij.rug.nl is WordPress with custom 'vacature' post type.
        # Articles have no <a> tags — URLs are JS-only. link_via_click=True: click each card,
        # capture page.url from the resulting navigation, go_back and repeat.
        # English listing (/en/all-vacancies/) shows only international/research positions.
        "source_name": "rug",
        "org_name": "University of Groningen",
        "tech": "playwright",
        "list_url": "https://werkenbij.rug.nl/en/all-vacancies/",
        "filter_params": {},
        "location": "Groningen, Nederland",
        "wait_selector": "article.vacature",
        "card_selector": "article.vacature",
        "title_sel": "h3.entry-title",
        "link_via_click": True,
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
        # Optional: click a button to trigger results (e.g. SuccessFactors "Search Jobs")
        pre_click_sel = self._cfg.get("pre_click")
        if pre_click_sel:
            try:
                btn = await page.wait_for_selector(pre_click_sel, timeout=12000)
                await btn.click()
            except Exception as e:
                self.logger.warning(f"[{self.source_name}] pre_click {pre_click_sel!r} failed: {e}")

        wait_sel = self._cfg.get("wait_selector", "article")
        try:
            await page.wait_for_selector(wait_sel, timeout=12000)
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

        link_via_click = self._cfg.get("link_via_click", False)

        if link_via_click:
            # Portal has no <a> on cards — click each by index, capture URL, go back.
            # Element handles become stale after navigation, so re-query per iteration.
            card_count = len(cards)
            for idx in range(card_count):
                try:
                    fresh_cards = await page.query_selector_all(self._cfg["card_selector"])
                    if idx >= len(fresh_cards):
                        break
                    card = fresh_cards[idx]

                    # Stop when pagination hides remaining cards (RUG hash-paging)
                    if not await card.is_visible():
                        break

                    title_el = await card.query_selector(self._cfg["title_sel"])
                    if not title_el:
                        continue
                    title = (await title_el.inner_text()).strip()
                    if not title:
                        continue
                    raw_text = (await card.inner_text()).strip()

                    async with page.expect_navigation(timeout=10000):
                        await card.click(timeout=8000)
                    full_url = self.canonicalize_url(page.url)
                    if full_url in seen:
                        await page.go_back(timeout=8000, wait_until="domcontentloaded")
                        await page.wait_for_selector(self._cfg["card_selector"], timeout=8000)
                        continue
                    seen.add(full_url)

                    detail_body = await page.query_selector("main, article, [class*='vacancy'], body")
                    if detail_body:
                        raw_text = (await detail_body.inner_text()).strip()

                    await page.go_back(timeout=8000, wait_until="domcontentloaded")
                    await page.wait_for_selector(self._cfg["card_selector"], timeout=8000)

                    jobs.append(RawJob(
                        title=title,
                        url=full_url,
                        source=self.source_name,
                        raw_text=raw_text[:4000],
                        organization=org,
                        location=location,
                    ))
                except Exception as e:
                    self.logger.warning(f"[{self.source_name}] click-navigate [{idx}] failed: {e}")
                    try:
                        if page.url != self.base_url:
                            await page.go_back(timeout=6000, wait_until="domcontentloaded")
                            await page.wait_for_selector(self._cfg["card_selector"], timeout=6000)
                    except Exception:
                        pass
        else:
            title_sel = self._cfg.get("title_sel", "")
            for card in cards:
                # Title — if title_sel is empty, card IS the title element (e.g. card is <a>)
                if title_sel:
                    title_el = await card.query_selector(title_sel)
                    if not title_el:
                        continue
                    title = (await title_el.inner_text()).strip()
                else:
                    title = (await card.inner_text()).strip().split("\n")[0]
                if not title:
                    continue

                raw_text = (await card.inner_text()).strip()

                # URL — try card's own href (handles case where card IS an <a>), then child <a>
                href = await card.get_attribute("href") or ""
                if not href:
                    a_el = await card.query_selector("a[href]")
                    href = await a_el.get_attribute("href") if a_el else ""
                if not href or href in seen:
                    continue
                seen.add(href)
                full_url = href if href.startswith("http") else urljoin(self.base_url, href)

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
