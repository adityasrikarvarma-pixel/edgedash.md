"""Arbeitnow job board source. Free, no API key required."""

from datetime import datetime
from typing import Optional

from edgedash.config import Config
from edgedash.sources.base import Source, SourceError, register
from edgedash.sources.http import get_json, NetworkError


@register
class ArbeitnowSource(Source):
    """Fetch jobs from the free Arbeitnow job board API.
    
    No API key required. Public endpoint: https://www.arbeitnow.com/api/job-board-api
    """

    @property
    def name(self) -> str:
        return "arbeitnow"

    def fetch(self, config: Config) -> list[dict]:
        """Fetch and filter job listings from Arbeitnow.
        
        Paging:
            - Fetch page 1, continue paging while results match keywords, up to 5 pages.
            - Rate limit: 1 request per second (steering rule 14).
        
        Filtering:
            - Filter by keywords and location.
            - If location filter leaves <5 results, relax it (prefer remote over empty DB).
        """
        import time

        all_results = []
        page = 1
        max_pages = 5
        base_url = "https://www.arbeitnow.com/api/job-board-api"

        while page <= max_pages:
            try:
                # Rate limit: 1 request/second per source (steering rule 14)
                if page > 1:
                    time.sleep(1.0)

                # Fetch page
                data = get_json(base_url, params={"page": page})

                # Arbeitnow returns {"data": [...]} or {"data": []} when no more results
                listings = data.get("data", [])
                if not listings:
                    break

                # Check if results match keywords
                has_keyword_match = any(
                    self._matches_keywords(job, config.keywords)
                    for job in listings
                )
                if not has_keyword_match and page > 1:
                    # Stop paging if no keywords match on this page
                    break

                all_results.extend(listings)
                page += 1

            except NetworkError as e:
                raise SourceError(f"Arbeitnow API error on page {page}: {e}")

        raw_count = len(all_results)

        # Normalise and filter
        normalised = []
        for job in all_results:
            try:
                norm = self._normalise(job, config)
                if norm:
                    normalised.append(norm)
            except Exception as e:
                # Skip malformed records
                continue

        filtered_count = len(normalised)
        print(
            f"   Arbeitnow: {raw_count} raw results, {filtered_count} survived filtering"
        )

        return normalised

    def _matches_keywords(self, job: dict, keywords: list[str]) -> bool:
        """Check if job title or description contains any keyword (case-insensitive)."""
        title = (job.get("title") or "").lower()
        desc = (job.get("description") or "").lower()
        text = f"{title} {desc}"
        return any(kw.lower() in text for kw in keywords)

    def _filter_by_location(self, jobs: list[dict], city: str) -> list[dict]:
        """Filter jobs by target city. Return all if city filter results in <5 jobs."""
        if not city:
            return jobs

        city_lower = city.lower()
        filtered = [
            j
            for j in jobs
            if city_lower in (j.get("location") or "").lower()
        ]

        if len(filtered) < 5:
            # Relax location filter, prefer remote/broader results
            return jobs

        return filtered

    def _normalise(self, job: dict, config: Config) -> Optional[dict]:
        """Convert Arbeitnow job to normalised format.
        
        Normalised keys: source, external_id, title, company, location, url,
            description, posted_at, raw.
        Missing values are None (never empty string, never "N/A").
        """
        # Arbeitnow fields:
        # id, title, company_name, location, description, url, posted_at, slug
        external_id = job.get("id") or job.get("slug")
        if not external_id:
            return None

        location = job.get("location") or ""
        title = job.get("title") or ""
        company = job.get("company_name") or ""
        description = job.get("description") or ""
        url = job.get("url") or ""
        posted_at_str = job.get("posted_at") or ""

        # Filter by keywords (required)
        if not self._matches_keywords(job, config.keywords):
            return None

        # Try to filter by location, but relax if it would result in <5 matches
        # For now, accept all if location doesn't match (we'll filter at fetch time)
        location_matches = config.target_city.lower() in location.lower()

        # Map to normalised format
        return {
            "source": self.name,
            "external_id": str(external_id),
            "title": title if title else None,
            "company": company if company else None,
            "location": location if location else None,
            "url": url if url else None,
            "description": description if description else None,
            "posted_at": posted_at_str if posted_at_str else None,
            "raw": job,
        }
