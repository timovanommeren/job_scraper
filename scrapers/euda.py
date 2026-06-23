# DISABLED — euda.europa.eu blocks all automated access via Cloudflare.
# Verified 2026-06-23: the ENTIRE euda.europa.eu domain (not just the jobs path) sits
# behind a Cloudflare "Just a moment..." JS challenge. Plain requests returns HTTP 403
# with a challenge-platform body for every path tried — /about/jobs_en, /calls_en,
# /sitemap.xml, and the site root. Headless Playwright (Chromium) also fails to clear the
# challenge: after loading the page and waiting 8s the title remains "Just a moment..."
# and no job content renders. This is the same infrastructure-level block as UN Careers
# (issue #2), not a code bug.
#
# Probe results (2026-06-23):
#   https://www.euda.europa.eu/about/jobs_en   -> 403, Cloudflare challenge
#   https://www.euda.europa.eu/calls_en        -> 403, Cloudflare challenge
#   https://www.euda.europa.eu/sitemap.xml     -> 403, Cloudflare challenge
#   https://e-recruitment.euda.europa.eu/      -> 403, Cloudflare challenge
#   recruitment.euda.europa.eu                 -> DNS does not resolve
#   Playwright headless on /about/jobs_en      -> stuck on "Just a moment..."
#
# Potential future workarounds (re-probe periodically — Cloudflare blocks can be stale,
# cf. OECD/BIT re-enabled 2026-06-22):
#   - A residential-IP or non-headless browser may clear the JS challenge.
#   - EUDA traineeships/SNE calls may be cross-posted to EU Careers (eu-careers.europa.eu)
#     or EURAXESS — check whether existing scrapers already catch them.
#   - Watch for a public JSON/API endpoint behind the Cloudflare-gated HTML, as was the
#     case for OECD (SmartRecruiters).
#
# Tracked in GitHub issue (see CLAUDE.md "Disabled Scrapers" / "Open GitHub Issues").

from .base import BaseScraper


class EudaScraper(BaseScraper):
    source_name = "euda"
    base_url = "https://www.euda.europa.eu/about/jobs_en"

    def fetch(self) -> list:
        self.logger.warning(
            "[euda] Scraper disabled: euda.europa.eu blocks all automated access via "
            "Cloudflare (HTTP 403 / JS challenge, domain-wide; headless Playwright also "
            "fails). Returning []."
        )
        return []
