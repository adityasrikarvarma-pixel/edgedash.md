"""
GapAnalyzer agent.
Deterministic — no LLM call anywhere in this file. No network.

opportunity_cost = sum(listing.fit_score / 100 for each blocked listing)

Why not raw frequency? Say kubernetes and terraform each appear in 2 listings.
  kubernetes: scores 82, 79  →  cost 1.61   (jobs you'd get interviews for)
  terraform:  scores 31, 24  →  cost 0.55   (everything is blocking those)
Same count. Three times the weight. Rule 24.
"""

import json
import logging
import uuid
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING

from edgedash.agents.base import Agent, AgentResult
from edgedash.agents.extractor import extract
from edgedash.skills import canonical

if TYPE_CHECKING:
    from edgedash.config import Config
    import edgedash.storage as storage_type

logger = logging.getLogger(__name__)

_LOW_CONFIDENCE_THRESHOLD = 3   # fewer than this → flagged (rule 27)
_TOP_N_GAPS = 10                # gaps reported
_MAX_EXAMPLE_IDS = 5            # per gap (rule 26)


class GapAnalyzer(Agent):
    """Analyse scored listings, compute weighted skill gaps, write snapshot."""

    @property
    def name(self) -> str:
        return "gap_analyzer"

    def run(self, config: "Config", storage, stop_conditions=None) -> AgentResult:
        db_path = config.db_path
        aliases = getattr(config, "skill_aliases", {}) or {}

        # Canonicalise the user's own skill set once
        my_skills: set[str] = {
            canonical(s, aliases)
            for s in (config.my_skills or [])
            if s.strip()
        }

        # ----------------------------------------------------------------
        # 1. Load every scored listing
        # ----------------------------------------------------------------
        listings = storage.get_scored_listings(db_path)
        if not listings:
            return AgentResult(
                agent=self.name,
                status="ok",
                records_touched=0,
                notes="no scored listings to analyse",
            )

        # ----------------------------------------------------------------
        # 2. Accumulate gap data per canonical skill
        # ----------------------------------------------------------------
        # For each skill: list of (listing_id, fit_score) tuples
        blocked_by: dict[str, list[tuple[str, int]]] = defaultdict(list)
        # Count where skill appears only as nice_to_have (not required)
        nice_only_count: dict[str, int] = defaultdict(int)

        analysed = 0
        for listing in listings:
            listing_id = listing["id"]
            score = listing.get("fit_score")
            if score is None:
                continue

            try:
                facts = extract(listing, db_path)
            except Exception as exc:
                logger.warning("gap_analyzer: skipping %s — extract failed: %s", listing_id, exc)
                continue

            required = [
                canonical(s, aliases)
                for s in (facts.get("required_skills") or [])
                if s.strip()
            ]
            nice = {
                canonical(s, aliases)
                for s in (facts.get("nice_to_have") or [])
                if s.strip()
            }

            required_set = set(required)
            missing_required = required_set - my_skills

            for skill in missing_required:
                if skill:  # skip empty strings from canonical()
                    blocked_by[skill].append((listing_id, score))

            # Track skills that appear ONLY in nice_to_have for this listing
            # (never mixed into required gaps — rule spec)
            nice_only = nice - required_set - my_skills
            for skill in nice_only:
                if skill:
                    nice_only_count[skill] += 1

            analysed += 1

        if not blocked_by:
            return AgentResult(
                agent=self.name,
                status="ok",
                records_touched=0,
                notes=f"no gaps found · {analysed} listings analysed",
            )

        # ----------------------------------------------------------------
        # 3. Compute metrics per gap skill
        # ----------------------------------------------------------------
        gap_rows: list[dict] = []

        for skill, occurrences in blocked_by.items():
            scores = [s for _, s in occurrences]
            n = len(scores)

            # Rule 24: rank by opportunity_cost, not raw frequency
            opportunity_cost = sum(s / 100.0 for s in scores)
            mean_score = sum(scores) / n
            top_score = max(scores)

            # Rule 26: up to 5 listing IDs, highest score first
            sorted_occ = sorted(occurrences, key=lambda t: t[1], reverse=True)
            example_ids = [lid for lid, _ in sorted_occ[:_MAX_EXAMPLE_IDS]]

            gap_rows.append(
                {
                    "skill":             skill,
                    "listings_blocked":  n,
                    "opportunity_cost":  round(opportunity_cost, 4),
                    "mean_score":        round(mean_score, 1),
                    "top_score":         top_score,
                    "also_nice_to_have": nice_only_count.get(skill, 0),
                    "example_ids":       json.dumps(example_ids),
                    # Rule 27: flag low-confidence gaps
                    "low_confidence":    1 if n < _LOW_CONFIDENCE_THRESHOLD else 0,
                }
            )

        # ----------------------------------------------------------------
        # 4. Rank by opportunity_cost DESC, keep top N
        # ----------------------------------------------------------------
        gap_rows.sort(key=lambda r: r["opportunity_cost"], reverse=True)
        top_gaps = gap_rows[:_TOP_N_GAPS]

        # ----------------------------------------------------------------
        # 5. Write timestamped snapshot (rule 25 — never overwrite)
        # ----------------------------------------------------------------
        run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:6]
        computed_at = datetime.utcnow().isoformat()

        written = storage.write_gap_snapshot(db_path, run_id, computed_at, top_gaps)

        # ----------------------------------------------------------------
        # 6. Build AgentResult notes
        # ----------------------------------------------------------------
        if top_gaps:
            best = top_gaps[0]
            top_desc = (
                f"{best['skill']} "
                f"({best['listings_blocked']} listings, "
                f"cost {best['opportunity_cost']:.1f})"
            )
        else:
            top_desc = "none"

        notes = (
            f"{len(top_gaps)} gaps"
            f" · top: {top_desc}"
            f" · {analysed} listings analysed"
        )

        logger.info("gap_analyzer: %s", notes)

        return AgentResult(
            agent=self.name,
            status="ok",
            records_touched=written,
            notes=notes,
        )
