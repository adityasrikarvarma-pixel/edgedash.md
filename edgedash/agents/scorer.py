"""
Scorer agent.
Selects unscored listings, extracts facts (from cache or LLM),
scores deterministically, and persists results.

No model calls in this file.  Scoring arithmetic lives in edgedash/scoring.py.
"""

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from edgedash.agents.base import Agent, AgentResult
from edgedash.agents.extractor import extract
from edgedash.scoring import score_listing

if TYPE_CHECKING:
    from edgedash.config import Config
    from edgedash import storage as storage_module_type

logger = logging.getLogger(__name__)


class Scorer(Agent):
    """Score unscored listings using deterministic, model-free arithmetic."""

    @property
    def name(self) -> str:
        return "scorer"

    def run(self, config: "Config", storage, stop_conditions=None) -> AgentResult:  # type: ignore[override]
        started_at = datetime.utcnow().isoformat()
        db_path    = config.db_path

        # Rule 29: Orchestrator-set limit takes precedence over config default
        batch_size = (
            stop_conditions.max_items
            if stop_conditions and stop_conditions.max_items is not None
            else getattr(config, "score_batch_size", 25)
        )

        # --- Rule 18/21: select only unscored listings, bounded batch ---
        listings = storage.get_unscored_listings(db_path, limit=batch_size)

        if not listings:
            return AgentResult(
                agent=self.name,
                status="ok",
                records_touched=0,
                notes="no unscored listings",
            )

        scored_count  = 0
        failed_count  = 0
        score_values: list[int] = []

        for listing in listings:
            listing_id = listing.get("id", "?")
            # --- Rule 17: per-listing try/except ---
            try:
                facts  = extract(listing, db_path)
                result = score_listing(listing, facts, config)

                storage.update_score(
                    db_path,
                    listing_id,
                    result["score"],
                    result["reason"],
                    result["components"],
                )

                score_values.append(result["score"])
                scored_count += 1
                logger.debug(
                    "scored %s → %d  %s",
                    listing_id,
                    result["score"],
                    result["reason"],
                )

            except Exception as exc:  # noqa: BLE001
                failed_count += 1
                logger.warning("scorer skipped listing %s: %s", listing_id, exc)

        # --- Rule 20: distribution log ---
        notes = _distribution_notes(score_values, failed_count)
        logger.info(notes)

        return AgentResult(
            agent=self.name,
            status="ok",
            records_touched=scored_count,
            notes=notes,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _distribution_notes(scores: list[int], failed: int) -> str:
    """
    Build the distribution summary string.  Rule 20: count, min, max, mean,
    spread.  Flag "suspect" when spread < 10.

    Example output:
        "scored 25 · range 31-89 · mean 58 · 2 failed · spread OK"
    """
    if not scores:
        suffix = f" · {failed} failed" if failed else ""
        return f"scored 0{suffix}"

    lo   = min(scores)
    hi   = max(scores)
    mean = round(sum(scores) / len(scores))
    spread = hi - lo

    spread_label = "spread suspect" if spread < 10 else "spread OK"
    if spread < 10:
        logger.warning(
            "Scorer spread is %d (< 10) — weights or input may be degenerate", spread
        )

    fail_part = f" · {failed} failed" if failed else ""
    return (
        f"scored {len(scores)}"
        f" · range {lo}-{hi}"
        f" · mean {mean}"
        f"{fail_part}"
        f" · {spread_label}"
    )
