"""
System state reader. Deterministic — no LLM, no network.

Reads counts and max(timestamp) from the database via storage only (Rule 2).
Never calls datetime.now() internally — `now` is always a parameter so
this is fully testable without patching.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

from edgedash import storage

if TYPE_CHECKING:
    from edgedash.config import Config


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    """Parse an ISO 8601 string to a UTC-aware datetime. Returns None if ts is None."""
    if not ts:
        return None
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass(frozen=True)
class SystemState:
    """
    A snapshot of the system at a point in time.

    All timestamps are UTC-aware datetimes or None.
    All counts are integers.
    This is a value object — immutable, no behaviour.
    """
    # Fetch
    last_fetch_at: Optional[datetime]
    hours_since_fetch: Optional[float]   # None if never fetched

    # Scoring
    unscored_count: int

    # Gap analysis
    gaps_computed_at: Optional[datetime]
    gaps_stale: bool   # True if any score is newer than the gap snapshot

    # Last cycle
    last_cycle_verdict: Optional[str]    # e.g. "ok", "partial", "failed"
    last_cycle_at: Optional[datetime]


def read_state(config: "Config", now: datetime) -> SystemState:
    """
    Read current system state from the database.

    Args:
        config: Loaded Config (provides db_path).
        now:    The current time as a UTC-aware datetime. NEVER call
                datetime.now() here — always pass it in so tests can
                control the clock.

    Returns:
        SystemState populated from cheap COUNT / MAX queries only.
        No full table loads.
    """
    db_path = config.db_path

    # --- fetch timing ---
    last_fetch_ts = storage.last_fetch_time(db_path)
    last_fetch_at = _parse_iso(last_fetch_ts)
    if last_fetch_at is not None:
        hours_since_fetch = (now - last_fetch_at).total_seconds() / 3600.0
    else:
        hours_since_fetch = None

    # --- unscored count ---
    unscored_count = storage.count_unscored(db_path)

    # --- gap staleness ---
    gap_ts = storage.last_gap_computed_at(db_path)
    gaps_computed_at = _parse_iso(gap_ts)

    scored_ts = storage.last_scored_at(db_path)
    last_scored_at_dt = _parse_iso(scored_ts)

    if gaps_computed_at is None:
        # Never computed — always stale
        gaps_stale = True
    elif last_scored_at_dt is not None and last_scored_at_dt > gaps_computed_at:
        # A score is newer than the last gap run
        gaps_stale = True
    else:
        gaps_stale = False

    # --- last cycle ---
    verdict, cycle_ts = storage.last_cycle_summary(db_path)
    last_cycle_at = _parse_iso(cycle_ts)

    return SystemState(
        last_fetch_at=last_fetch_at,
        hours_since_fetch=hours_since_fetch,
        unscored_count=unscored_count,
        gaps_computed_at=gaps_computed_at,
        gaps_stale=gaps_stale,
        last_cycle_verdict=verdict,
        last_cycle_at=last_cycle_at,
    )
