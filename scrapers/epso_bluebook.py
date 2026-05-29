# EPSO Blue Book Traineeship scraper.
# Portal: https://traineeships.ec.europa.eu/index_en
# The Blue Book is the European Commission's in-house traineeship programme —
# separate from eu-careers.europa.eu (agency traineeships covered by eucareers.py).
#
# Application windows open twice a year, roughly March and October.
# The homepage shows a status banner:
#   "Registration for the [Month Year] session" + "is open." or "is closed."
# Verified 2026-05-29: server-rendered HTML, requests + BS4 sufficient.
# Selector: section.ecl-banner--no-media  →  .ecl-banner__title-text / .ecl-banner__description-text
#
# When closed: returns [] and logs INFO (not WARNING — this is expected seasonal behaviour).
# When open: returns one RawJob whose URL includes the session slug so that
#   the March session and October session produce distinct dedup hashes.

import re
import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from .base import BaseScraper, RawJob

_BASE = "https://traineeships.ec.europa.eu"
_INDEX = f"{_BASE}/index_en"


class EPSOBluebookScraper(BaseScraper):
    source_name = "epso_bluebook"
    base_url = _INDEX

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }

    def fetch(self) -> list:
        try:
            return self._scrape()
        except Exception:
            self.logger.exception("[EPSO Blue Book] Scraper failed")
            return []

    def _scrape(self) -> list:
        soup = self._get_soup(_INDEX)

        # The status banner is the ecl-banner with no background image (--no-media).
        # It contains the session name and open/closed status.
        status_banner = soup.select_one("section.ecl-banner--no-media")
        if not status_banner:
            self.logger.info(
                "[EPSO Blue Book] Status banner not found — 0 opportunities returned. "
                "Expected outside the March and October application windows."
            )
            return []

        title_el = status_banner.select_one(".ecl-banner__title-text")
        desc_el = status_banner.select_one(".ecl-banner__description-text")
        session_text = title_el.get_text(strip=True) if title_el else ""
        status_text = desc_el.get_text(strip=True) if desc_el else ""

        is_open = "is open" in status_text.lower()

        if not is_open:
            self.logger.info(
                f"[EPSO Blue Book] Applications closed ({session_text}: {status_text}) — "
                "0 opportunities returned. Expected outside the March and October windows."
            )
            return []

        # Build a session slug for the URL so March and October sessions have
        # distinct dedup hashes (e.g. "march-2027" vs "october-2026").
        session_slug = re.sub(r"[^a-z0-9]+", "-", session_text.lower()).strip("-")
        opportunity_url = f"{_INDEX}?session={session_slug}"

        raw_text = (
            f"Blue Book Traineeship — {session_text}: {status_text}\n\n"
            "The Blue Book is the European Commission's 5-month paid traineeship programme. "
            "Trainees gain hands-on experience in EU policymaking and administration. "
            "Placements are available at Commission departments, executive agencies, "
            "decentralised agencies and the European External Action Service, "
            "mainly in Brussels and Luxembourg.\n\n"
            "Types: Administrative Traineeship, Translation Traineeship.\n"
            f"Apply at: {_INDEX}"
        )

        self.logger.info(f"[EPSO Blue Book] Applications open — {session_text}")
        return [
            RawJob(
                title=f"Blue Book Traineeship — {session_text}",
                url=self.canonicalize_url(opportunity_url),
                source=self.source_name,
                raw_text=raw_text[:4000],
                organization="European Commission",
                location="Brussels / Luxembourg (EU)",
            )
        ]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
    def _get_soup(self, url: str) -> BeautifulSoup:
        resp = requests.get(url, headers=self.HEADERS, timeout=20)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")
