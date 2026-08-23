"""Real Fetcher agent. Fetches from enabled job sources via the source registry."""

from typing import TYPE_CHECKING

from edgedash.agents.base import Agent, AgentResult
from edgedash.sources import get_source
from edgedash.sources.base import SourceError
from edgedash import storage

if TYPE_CHECKING:
    from edgedash.config import Config


class Fetcher(Agent):
    """Fetch job listings from enabled sources. Per-source error handling (rule 12)."""

    @property
    def name(self) -> str:
        return "Fetcher"

    def run(self, config: "Config", storage_module, stop_conditions=None) -> AgentResult:
        """Fetch from all enabled sources. One failure must not stop others."""
        # Respect stop conditions from Orchestrator (rule 29)
        max_listings = (
            stop_conditions.max_items
            if stop_conditions and stop_conditions.max_items is not None
            else getattr(config, "fetch_max_listings", 200)
        )

        all_rows = []
        source_results = []
        total_new = 0

        for source_name in config.sources:
            try:
                source = get_source(source_name)
                if not source:
                    source_results.append(f"{source_name}: NOT FOUND")
                    continue

                rows = source.fetch(config)
                rows = rows[:max_listings]   # honour stop condition cap
                new_count = storage_module.upsert_listings(
                    config.db_path, rows
                )
                total_new += new_count
                all_rows.extend(rows)

                source_results.append(f"{source_name}: {len(rows)} rows ({new_count} new)")

            except SourceError as e:
                # Per-source failure: log and continue (steering rule 12)
                print(f"   ⚠ {source_name} FAILED: {e}")
                storage_module.log_cycle(
                    config.db_path,
                    agent=f"{source_name} (source)",
                    started_at=__import__("datetime").datetime.utcnow().isoformat(),
                    finished_at=__import__("datetime").datetime.utcnow().isoformat(),
                    records_touched=0,
                    status="failed",
                    notes=str(e),
                )
                source_results.append(f"{source_name}: FAILED ({str(e)[:20]}...)")

        notes = " | ".join(source_results)
        return AgentResult(
            agent=self.name,
            status="ok",
            records_touched=total_new,
            notes=notes,
        )
