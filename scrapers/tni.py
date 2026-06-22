# DISABLED — tni.org (Drupal CMS) returns HTTP 429 to every automated request.
# Verified 2026-05-28 and re-confirmed 2026-06-22: the block is IP/rate-based,
# not User-Agent-based — header changes, jitter, and reduced retries all failed,
# and even the lightweight RSS path (tni.org/rss) returns 429. The only thing
# that would clear it is routing through a residential proxy, which isn't worth
# the cost/upkeep for a source that rarely posts in-scope roles (#1).
#
# Decision (2026-06-22): stop scraping. TNI is on the manual weekly check list.
# Previously this ran live and burned ~50 s/run on jittered retries before failing;
# it now returns [] immediately, like the other disabled scrapers. If TNI ever
# becomes reachable (e.g. via proxy), the prior requests + BS4 implementation is
# in git history — re-verify the CSS selectors against the live DOM before reuse.

from .base import BaseScraper


class TNIScraper(BaseScraper):
    source_name = "tni"
    base_url = "https://www.tni.org/en/internships-jobs"

    def fetch(self) -> list:
        self.logger.warning(
            "[TNI] Scraper disabled: tni.org returns HTTP 429 (IP-level rate limit) "
            "to all automation; on the manual weekly check list. Returning []."
        )
        return []
