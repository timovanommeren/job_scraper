# Blue Book traineeships open ~twice per year (March and October).
# If 0 results are found outside these windows, that is expected behaviour.
#
# Page structure verified 2026-05-28:
#   Each traineeship is an <a href="/en/trainee/{slug}"> element whose text
#   contains the title as the first line, followed by location / deadline.
#   The full listing is at /en/traineeships-open (all agencies) but the
#   base page /en/job-opportunities/traineeships is good enough as the entry
#   point — it lists active slots and links to each detail page.

from .base import PlaywrightBaseScraper, RawJob

_BASE = "https://eu-careers.europa.eu"


class EuCareersScraper(PlaywrightBaseScraper):
    source_name = "eucareers"
    base_url = f"{_BASE}/en/traineeships-open"

    async def _extract_jobs(self, page) -> list:
        jobs = []
        # Do NOT use wait_for_load_state("networkidle") — it stalls for up to
        # 30 s on this CMS page without a JS-rendered SPA requirement.
        # Instead, wait up to 10 s for at least one traineeship link to appear.
        try:
            await page.wait_for_selector('a[href*="/trainee/"]', timeout=10000)
        except Exception:
            # Off-season: no open slots — log seasonal context, not an error.
            self.logger.info(
                "EU Careers: 0 traineeships found — this is expected outside "
                "the March and October intake windows."
            )
            return []

        links = await page.query_selector_all('a[href*="/trainee/"]')
        seen: set = set()
        for link in links:
            href = await link.get_attribute("href") or ""
            if not href:
                continue
            full_url = (_BASE + href) if href.startswith("/") else href
            if full_url in seen:
                continue
            seen.add(full_url)

            text = (await link.inner_text()).strip().replace("\xa0", " ")
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            title = lines[0] if lines else text[:80]

            jobs.append(RawJob(
                title=title,
                url=self.canonicalize_url(full_url),
                source=self.source_name,
                raw_text=text[:4000],
                organization="EU / European Agency",
            ))

        if jobs:
            self.logger.info(f"EU Careers: {len(jobs)} traineeship(s) found")
        else:
            self.logger.info(
                "EU Careers: 0 traineeships found — this is expected outside "
                "the March and October intake windows."
            )
        return jobs
