# DISABLED — concursos.fgv.br no longer exists.
# Verified 2026-05-28: URL redirects to portal.fgv.br which returns HTTP 404
# ("Página não encontrada"). SSL handshake also fails on portal.fgv.br.
#
# FGV may have moved their selection processes to a different URL.
# Check https://portal.fgv.br or https://fgv.br/carreiras manually
# before attempting to re-enable this scraper.

from .base import BaseScraper


class FGVScraper(BaseScraper):
    source_name = "fgv"
    base_url = "https://concursos.fgv.br/"

    def fetch(self) -> list:
        self.logger.warning(
            "[FGV] Scraper disabled: concursos.fgv.br is defunct (HTTP 404 after redirect). "
            "See scrapers/fgv.py for details. Returning []."
        )
        return []
