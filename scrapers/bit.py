# DISABLED — bi.team is behind Cloudflare bot detection.
# Verified 2026-05-28: all requests (headless Playwright included) trigger a
# Cloudflare challenge page. Additionally, the careers page showed 0 open positions.
#
# No external ATS (Greenhouse, Lever, Workable) was found — BIT appears to post
# jobs directly on their website without a public API.
#
# To re-enable: check https://www.bi.team/about-us/careers/ manually for new jobs
# and/or investigate whether BIT has added an ATS integration.

from .base import BaseScraper


class BITScraper(BaseScraper):
    source_name = "bit"
    base_url = "https://bi.team/about-us/careers/"

    def fetch(self) -> list:
        self.logger.warning(
            "[BIT] Scraper disabled: bi.team is behind Cloudflare bot protection "
            "and had 0 open positions at time of verification (2026-05-28). Returning []."
        )
        return []
