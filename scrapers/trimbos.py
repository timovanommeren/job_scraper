# Trimbos vacancy listing is JavaScript-rendered (React SPA) — Playwright required.
# Verified 2026-05-28: plain requests returns 0 vacancy links; Playwright finds
# 9 live vacancies after JS execution.
#
# Selector verified 2026-05-28:
#   Each vacancy is an <a href="/vacaturebeschrijving-{type}/{slug}"> element.
#   The link text contains the title as the first non-empty line, followed by
#   hours ("Aantal uur: N") and deadline ("Reageren tot en met: DD-MM-YYYY").
#   URL pattern: https://werkenbij.trimbos.nl/vacaturebeschrijving-regulier/{slug}
#                https://werkenbij.trimbos.nl/vacaturebeschrijving-stagiair/{slug}

from .base import PlaywrightBaseScraper, RawJob

_BASE = "https://werkenbij.trimbos.nl"


class TrimbosScraper(PlaywrightBaseScraper):
    source_name = "trimbos"
    base_url = f"{_BASE}/werkenbij-website/vacatures"

    async def _extract_jobs(self, page) -> list:
        jobs = []
        # Wait for at least one vacancy link to appear (cap at 10 s)
        try:
            await page.wait_for_selector(
                'a[href*="vacaturebeschrijving"]',
                timeout=10000,
            )
        except Exception:
            self.logger.info("Trimbos: no vacaturebeschrijving links appeared within 10 s — 0 vacancies")
            return []

        cards = await page.query_selector_all('a[href*="vacaturebeschrijving"]')
        seen_hrefs: set = set()
        for card in cards:
            href = await card.get_attribute("href") or ""
            if not href:
                continue
            full_url = (_BASE + href) if href.startswith("/") else href
            if full_url in seen_hrefs:
                continue
            seen_hrefs.add(full_url)

            text = (await card.inner_text()).strip().replace("\xa0", " ")
            # First non-empty line is the title
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            title = lines[0] if lines else text[:80]

            jobs.append(RawJob(
                title=title,
                url=self.canonicalize_url(full_url),
                source=self.source_name,
                raw_text=text[:4000],
                organization="Trimbos-instituut",
                location="Utrecht / Den Haag, Nederland",
            ))

        self.logger.info(f"Trimbos: {len(jobs)} vacancies found")
        return jobs
