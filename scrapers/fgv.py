# FGV (Fundação Getulio Vargas) careers scraper.
# Portal: https://portal.fgv.br/trabalhe-conosco — Drupal 10, server-rendered.
# Verified 2026-05-29: page loads in Chromium (200), but portal.fgv.br rejects
# Python's TLS handshake (SSLV3_ALERT_HANDSHAKE_FAILURE), so Playwright is used.
# Selector confirmed via Chromium:
#   ul.list-unstyled a[href^="/vaga/"]  →  job title links
#   .views-field-field-vaga-local .field-content  →  location
#   .views-field-body .field-content  →  description snippet

from .base import PlaywrightBaseScraper, RawJob

_BASE = "https://portal.fgv.br"


class FGVScraper(PlaywrightBaseScraper):
    source_name = "fgv"
    base_url = f"{_BASE}/trabalhe-conosco"

    async def _extract_jobs(self, page) -> list:
        try:
            await page.wait_for_selector("a[href^='/vaga/']", timeout=10000)
        except Exception:
            self.logger.info("[FGV] 0 jobs found — no vacancy links on page")
            return []

        links = await page.query_selector_all("a[href^='/vaga/']")
        seen: set = set()
        jobs = []

        for link in links:
            href = await link.get_attribute("href") or ""
            if not href:
                continue
            full_url = _BASE + href
            canonical = self.canonicalize_url(full_url)
            if canonical in seen:
                continue
            seen.add(canonical)

            title = (await link.inner_text()).strip()
            if not title:
                continue

            # Walk up to the card container for location + description text.
            card = await link.evaluate_handle(
                "el => el.closest('.card') || el.closest('li')"
            )
            raw_text = title
            location = None
            if card:
                raw_text = (await card.as_element().inner_text()).strip()[:4000] if card.as_element() else title
                loc_el = await card.as_element().query_selector(
                    ".views-field-field-vaga-local .field-content"
                ) if card.as_element() else None
                if loc_el:
                    location = (await loc_el.inner_text()).strip() or None

            jobs.append(RawJob(
                title=title,
                url=canonical,
                source=self.source_name,
                raw_text=raw_text[:4000],
                organization="FGV — Fundação Getulio Vargas",
                location=location,
            ))

        if jobs:
            self.logger.info(f"[FGV] {len(jobs)} job(s) found")
        else:
            self.logger.info("[FGV] 0 jobs found")
        return jobs
