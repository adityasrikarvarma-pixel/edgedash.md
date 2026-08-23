"""
Deterministic, model-free scoring.
Pure functions only. No imports from llm.py. No network.
"""

from datetime import datetime, timezone
from typing import Optional


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_listing(listing: dict, facts: dict, config) -> dict:
    """
    Score a job listing deterministically from extracted facts.

    Args:
        listing: row dict from the listings table (has location, posted_at, …)
        facts:   extraction dict (required_skills, nice_to_have, seniority, remote_ok)
        config:  Config dataclass (my_skills, target_seniority, target_city,
                 score_weight_*, …)

    Returns:
        {
          "score":      int  0-100,
          "reason":     str  compact human-readable string,
          "components": {"skill_match": float, "seniority_fit": float,
                         "location_fit": float, "recency": float}
        }
    """
    w_skill      = getattr(config, "score_weight_skill",      0.45)
    w_seniority  = getattr(config, "score_weight_seniority",  0.25)
    w_location   = getattr(config, "score_weight_location",   0.15)
    w_recency    = getattr(config, "score_weight_recency",    0.15)

    skill_score     = _score_skill_match(facts, config)
    seniority_score = _score_seniority_fit(facts, config)
    location_score  = _score_location_fit(facts, listing, config)
    recency_score   = _score_recency(listing)

    weighted = (
        skill_score     * w_skill
        + seniority_score * w_seniority
        + location_score  * w_location
        + recency_score   * w_recency
    )

    final_score = max(0, min(100, round(weighted * 100)))

    components = {
        "skill_match":   round(skill_score,     3),
        "seniority_fit": round(seniority_score, 3),
        "location_fit":  round(location_score,  3),
        "recency":       round(recency_score,    3),
    }

    reason = build_reason(components, facts, config, listing)

    return {"score": final_score, "reason": reason, "components": components}


def build_reason(components: dict, facts: dict, config, listing: dict) -> str:
    """
    Assemble a compact, human-readable explanation from the computed numbers.

    Style:
        "4/6 required skills · seniority fits · remote · posted 2d ago · gap: kubernetes, spark"

    The gap list is what the next-session Gap Analyzer reads — always name
    the actual missing skills, not a count.
    """
    parts: list[str] = []

    user_skills = {s.lower() for s in (getattr(config, "my_skills", None) or [])}
    required    = facts.get("required_skills") or []
    nice        = facts.get("nice_to_have")    or []

    # --- skill match ---
    if required:
        matched = sum(1 for s in required if s.lower() in user_skills)
        parts.append(f"{matched}/{len(required)} required skills")
    else:
        parts.append("no required skills listed")

    # --- seniority ---
    s = components.get("seniority_fit", 0.5)
    if s >= 1.0:
        parts.append("seniority fits")
    elif s >= 0.6:
        parts.append("seniority close")
    elif s >= 0.25:
        parts.append("seniority off")
    else:
        parts.append("seniority way off")

    # --- location ---
    remote_ok = facts.get("remote_ok")
    loc_raw   = listing.get("location") or ""
    loc_lower = loc_raw.lower()
    city      = (getattr(config, "target_city", "") or "").lower()

    if remote_ok is True:
        parts.append("remote")
    elif city and city in loc_lower:
        parts.append(f"in {config.target_city}")
    elif not loc_lower.strip():
        parts.append("location unknown")
    else:
        parts.append(f"location: {loc_raw}")

    # --- recency ---
    posted_at = listing.get("posted_at")
    if not posted_at:
        parts.append("posted date unknown")
    else:
        days = _days_ago(posted_at)
        if days is None:
            parts.append("posted date unknown")
        elif days == 0:
            parts.append("posted today")
        elif days == 1:
            parts.append("posted 1d ago")
        else:
            parts.append(f"posted {days}d ago")

    # --- gap (most useful part — named skills, not counts) ---
    missing = [s for s in required if s.lower() not in user_skills]
    # Also surface nice-to-have gaps, de-duplicated, up to a total of 5
    nice_missing = [
        s for s in nice
        if s.lower() not in user_skills and s.lower() not in {m.lower() for m in missing}
    ]
    all_gaps = missing + nice_missing
    if all_gaps:
        shown = all_gaps[:5]
        gap_str = ", ".join(shown)
        if len(all_gaps) > 5:
            gap_str += f" +{len(all_gaps) - 5} more"
        parts.append(f"gap: {gap_str}")

    return " · ".join(parts)


# ---------------------------------------------------------------------------
# Component scorers  (private, but kept accessible for unit tests)
# ---------------------------------------------------------------------------

def _score_skill_match(facts: dict, config) -> float:
    """
    Fraction of required skills the user has, with nice-to-have at 1/3 weight.
    Case-insensitive. Empty required-skills edge case handled explicitly.
    Returns 0.0–1.0.
    """
    user_skills = {s.lower() for s in (getattr(config, "my_skills", None) or [])}
    required    = facts.get("required_skills") or []
    nice        = facts.get("nice_to_have")    or []

    # --- empty required skills ---
    if not required:
        if not nice:
            # Nothing specified → neutral full credit
            return 1.0
        # Only nice-to-have: score against those
        matched = sum(1 for s in nice if s.lower() in user_skills)
        return matched / len(nice)

    # --- normal case ---
    req_matched  = sum(1 for s in required if s.lower() in user_skills)
    nice_matched = sum(1 for s in nice     if s.lower() in user_skills)

    numerator   = req_matched  + nice_matched  / 3.0
    denominator = len(required) + len(nice)    / 3.0

    # denominator cannot be 0 here because required is non-empty
    return min(1.0, numerator / denominator)


def _score_seniority_fit(facts: dict, config) -> float:
    """
    Ordered seniority bands: junior=0, mid=1, senior=2, lead=3.
    Exact=1.0, 1 band away=0.6, 2=0.25, 3+=0.0, unknown→0.5.
    """
    _BAND = {"junior": 0, "mid": 1, "senior": 2, "lead": 3}

    fact_raw   = (facts.get("seniority") or "unknown").lower()
    target_raw = (getattr(config, "target_seniority", "mid") or "mid").lower()

    fact_level   = _BAND.get(fact_raw)
    target_level = _BAND.get(target_raw)

    if fact_level is None or target_level is None:
        return 0.5

    dist = abs(fact_level - target_level)
    return {0: 1.0, 1: 0.6, 2: 0.25}.get(dist, 0.0)


def _score_location_fit(facts: dict, listing: dict, config) -> float:
    """
    remote_ok True  → 1.0
    location matches target_city → 1.0
    location missing/empty → 0.5
    elsewhere + not remote → 0.1
    remote_ok None + location mismatch → 0.5  (benefit of the doubt)
    """
    remote_ok  = facts.get("remote_ok")
    loc_raw    = listing.get("location") or ""
    loc_lower  = loc_raw.lower()
    city       = (getattr(config, "target_city", "") or "").lower()

    if remote_ok is True:
        return 1.0

    if city and city in loc_lower:
        return 1.0

    if not loc_lower.strip():
        return 0.5

    if remote_ok is False:
        return 0.1

    # remote_ok is None, location present but doesn't match city
    return 0.5


def _score_recency(listing: dict) -> float:
    """
    Linear decay: today=1.0, 30 days=0.0.
    Null or unparseable posted_at → 0.5.  Never raises.
    """
    posted_at = listing.get("posted_at")
    if not posted_at:
        return 0.5

    days = _days_ago(posted_at)
    if days is None:
        return 0.5
    if days <= 0:
        return 1.0
    if days >= 30:
        return 0.0
    return 1.0 - days / 30.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _days_ago(posted_at) -> Optional[float]:
    """
    Return fractional days since posted_at, or None if unparseable.
    Handles both naive and tz-aware datetimes safely.
    """
    try:
        if isinstance(posted_at, str):
            dt = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
        elif isinstance(posted_at, datetime):
            dt = posted_at
        else:
            return None

        # Normalize naive datetimes to UTC timezone-aware objects
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        return (now - dt).total_seconds() / 86400.0

    except (ValueError, AttributeError, TypeError, OverflowError):
        return None