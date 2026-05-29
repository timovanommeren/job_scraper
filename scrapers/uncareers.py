# DISABLED — careers.un.org blocks all automated access via AWS CloudFront.
# Verified 2026-05-28: every request (plain requests, Playwright with headless Chromium)
# returns "403 ERROR — The request could not be satisfied" from CloudFront.
#
# Potential future workarounds:
#   - UN's Inspira system (inspira.un.org) — heavily JS-rendered, also locked down
#   - Check if UNDP, UNODC, or WHO expose their own vacancy pages independently

from .base import BaseScraper


class UnCareersScraper(BaseScraper):
    source_name = "uncareers"
    base_url = "https://careers.un.org/lbw/home.aspx?viewtype=SJ"

    def fetch(self) -> list:
        self.logger.warning(
            "[UNCAREERS] Scraper disabled: careers.un.org blocks all automated "
            "requests via CloudFront (HTTP 403). Returning []."
        )
        return []
