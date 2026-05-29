# DISABLED — oecd.org serves a Cloudflare bot-detection challenge page to all
# automated requests (headless browsers included).
# Verified 2026-05-28: page returns "Performing security verification / Ray ID: ..."
# regardless of User-Agent.
#
# Potential future workarounds:
#   - OECD's Taleo ATS (oecdcareers.taleo.net) — connection refused, not publicly accessible
#   - Check if OECD publishes an RSS/Atom feed for vacancies
#   - Monitor https://www.oecd.org/en/about/jobs.html manually for ATS link changes

from .base import BaseScraper


class OecdScraper(BaseScraper):
    source_name = "oecd"
    base_url = "https://www.oecd.org/careers/"

    def fetch(self) -> list:
        self.logger.warning(
            "[OECD] Scraper disabled: oecd.org is behind Cloudflare bot protection; "
            "every automated request receives a challenge page. Returning []."
        )
        return []
