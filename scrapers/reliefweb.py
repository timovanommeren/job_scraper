import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .base import BaseScraper, RawJob


class ReliefWebScraper(BaseScraper):
    source_name = "reliefweb"
    # ReliefWeb migrated from v1 to v2 in 2025. v2 requires a registered appname:
    # register at https://apidoc.reliefweb.int/parameters#appname then set reliefweb.appname in settings.yaml
    base_url = "https://api.reliefweb.int/v2/jobs"

    def fetch(self) -> list:
        try:
            return self._fetch_all_pages()
        except Exception:
            self.logger.exception("ReliefWeb scraper failed")
            return []

    def _fetch_page(self, offset: int = 0) -> dict:
        rw_cfg = self.settings.get("reliefweb", {})
        params = {
            "appname": rw_cfg.get("appname", "timo-job-scraper"),
            "limit": rw_cfg.get("limit", 50),
            "offset": offset,
            "fields[include][]": ["title", "body", "url", "date", "source", "city", "country", "type"],
            "filter[operator]": "AND",
            "filter[conditions][0][field]": "status",
            "filter[conditions][0][value]": "published",
        }
        resp = requests.get(self.base_url, params=params, timeout=15)
        if resp.status_code == 403:
            # 403 = appname not registered — this is a config error, not retryable
            self.logger.error(
                "ReliefWeb API returned 403: appname not yet approved. "
                "Register at https://apidoc.reliefweb.int/parameters#appname "
                "and update reliefweb.appname in config/settings.yaml"
            )
            return {}   # Return empty dict so fetch() returns [] without retrying
        resp.raise_for_status()
        return resp.json()

    def _fetch_all_pages(self) -> list:
        jobs = []
        offset = 0
        max_jobs = self.settings["scraper"].get("max_jobs_per_site", 100)

        while True:
            data = self._fetch_page(offset)
            items = data.get("data", [])
            if not items:
                break
            for item in items:
                f = item.get("fields", {})
                location_parts = []
                city = f.get("city")
                country = f.get("country")
                if city:
                    location_parts.append(city[0]["name"] if isinstance(city, list) else city)
                if country:
                    location_parts.append(country[0]["name"] if isinstance(country, list) else country)
                source_list = f.get("source") or []
                org = source_list[0].get("name", "") if source_list else ""
                jobs.append(RawJob(
                    title=f.get("title", ""),
                    url=self.canonicalize_url(item.get("href", f.get("url", ""))),
                    source=self.source_name,
                    raw_text=f.get("body", "")[:4000],
                    organization=org,
                    location=", ".join(location_parts),
                    deadline=f.get("date", {}).get("closing"),
                ))
            if len(items) < 50:
                break
            offset += 50
            if offset >= max_jobs:
                break
        return jobs
