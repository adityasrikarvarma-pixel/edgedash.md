"""
Tests for edgedash/scoring.py.
Pure functions — no network, no model calls, no fixtures beyond dataclasses.

Mandatory six cases (per spec):
  1. perfect_match
  2. zero_match
  3. empty_required_skills
  4. null_posted_at
  5. null_remote_ok
  6. seniority_three_bands_off
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest

from edgedash.scoring import (
    _score_location_fit,
    _score_recency,
    _score_seniority_fit,
    _score_skill_match,
    build_reason,
    score_listing,
)


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

@dataclass
class Cfg:
    """Minimal stand-in for Config. Uses same attribute names."""
    my_skills: list[str] = field(default_factory=lambda: ["Python", "SQL", "Tableau", "Excel"])
    target_seniority: str = "mid"
    target_city: str = "Bengaluru"
    score_weight_skill:     float = 0.45
    score_weight_seniority: float = 0.25
    score_weight_location:  float = 0.15
    score_weight_recency:   float = 0.15


def _listing(**kwargs) -> dict:
    """Base listing dict with safe defaults; override with kwargs."""
    base = {
        "id": "test",
        "title": "Analyst",
        "location": "",
        "posted_at": datetime.now(timezone.utc).isoformat(),
    }
    base.update(kwargs)
    return base


def _facts(**kwargs) -> dict:
    """Base facts dict with safe defaults; override with kwargs."""
    base = {
        "required_skills": [],
        "nice_to_have": [],
        "seniority": "mid",
        "remote_ok": None,
    }
    base.update(kwargs)
    return base


# ===========================================================================
# Mandatory spec cases (score_listing integration)
# ===========================================================================

class TestMandatoryCases:

    # 1. Perfect match ----------------------------------------------------
    def test_perfect_match(self):
        """All required skills present, exact seniority, remote, posted today → 100."""
        cfg = Cfg()
        listing = _listing(location="Remote", posted_at=datetime.now(timezone.utc).isoformat())
        facts = _facts(
            required_skills=["python", "sql"],
            seniority="mid",
            remote_ok=True,
        )
        result = score_listing(listing, facts, cfg)
        assert result["score"] == 100, f"Expected 100, got {result['score']}"
        assert "components" in result
        assert result["components"]["skill_match"] == 1.0
        assert result["components"]["seniority_fit"] == 1.0
        assert result["components"]["location_fit"] == 1.0

    # 2. Zero match -------------------------------------------------------
    def test_zero_match(self):
        """No required skills match, wrong seniority, wrong location, stale post → very low."""
        cfg = Cfg()
        # Use 35 days so recency is firmly 0.0 regardless of clock rounding
        stale = (datetime.now(timezone.utc) - timedelta(days=35)).isoformat()
        listing = _listing(location="San Francisco", posted_at=stale)
        facts = _facts(
            required_skills=["kubernetes", "rust", "go"],
            # lead is 2 bands from mid → seniority_fit=0.25, not 0.0
            # Use "junior" as target and "lead" as fact for a true 3-band gap (score=0.0)
            seniority="lead",
            remote_ok=False,
        )
        result = score_listing(listing, facts, cfg)
        # skill=0(0.45) + seniority=0.25(0.25) + location=0.1(0.15) + recency=0(0.15)
        # = 0 + 0.0625 + 0.015 + 0 = 0.0775 → score ≈ 8
        assert result["score"] <= 15, f"Expected low score, got {result['score']}"
        assert result["components"]["skill_match"] == 0.0
        assert result["components"]["recency"] == 0.0
        assert result["components"]["location_fit"] == 0.1

    # 3. Empty required_skills --------------------------------------------
    def test_empty_required_skills(self):
        """No required skills in listing → skill_match == 1.0 (no divide-by-zero)."""
        cfg = Cfg()
        facts = _facts(required_skills=[], nice_to_have=[])
        result = score_listing(_listing(), facts, cfg)
        assert result["components"]["skill_match"] == 1.0
        assert 0 <= result["score"] <= 100

    # 4. Null posted_at ---------------------------------------------------
    def test_null_posted_at(self):
        """posted_at is None → recency == 0.5, no crash."""
        cfg = Cfg()
        listing = _listing(posted_at=None)
        facts = _facts()
        result = score_listing(listing, facts, cfg)
        assert result["components"]["recency"] == 0.5
        assert 0 <= result["score"] <= 100

    # 5. Null remote_ok ---------------------------------------------------
    def test_null_remote_ok(self):
        """remote_ok is None, non-target city → location_fit == 0.5."""
        cfg = Cfg()
        listing = _listing(location="London")
        facts = _facts(remote_ok=None)
        result = score_listing(listing, facts, cfg)
        assert result["components"]["location_fit"] == 0.5
        assert 0 <= result["score"] <= 100

    # 6. Seniority three bands off ----------------------------------------
    def test_seniority_three_bands_off(self):
        """Target mid (1), listing lead (3) → distance 2, score 0.25. """
        # junior=0, mid=1, senior=2, lead=3  ← only 3 levels exist so max distance is 3
        cfg = Cfg(target_seniority="junior")
        facts = _facts(seniority="lead")     # distance = 3
        result = _score_seniority_fit(facts, cfg)
        assert result == 0.0, f"Expected 0.0 for 3-band gap, got {result}"

    def test_seniority_three_bands_off_in_composite(self):
        """Confirm 0.0 seniority propagates into final score."""
        cfg = Cfg(target_seniority="junior")
        facts = _facts(seniority="lead")
        result = score_listing(_listing(), facts, cfg)
        assert result["components"]["seniority_fit"] == 0.0


# ===========================================================================
# Skill match unit tests
# ===========================================================================

class TestSkillMatch:

    def test_partial_match_with_nice_to_have(self):
        """2/4 required + 1/2 nice_to_have at 1/3 weight."""
        cfg = Cfg(my_skills=["python", "sql", "tableau"])
        facts = _facts(
            required_skills=["python", "sql", "kubernetes", "spark"],
            nice_to_have=["tableau", "kafka"],
        )
        score = _score_skill_match(facts, cfg)
        # numerator = 2 + 1/3 = 2.333
        # denominator = 4 + 2/3 = 4.667
        expected = (2 + 1/3) / (4 + 2/3)
        assert abs(score - expected) < 0.001, f"Expected {expected:.3f}, got {score:.3f}"

    def test_case_insensitive(self):
        cfg = Cfg(my_skills=["PYTHON", "SQL"])
        facts = _facts(required_skills=["python", "sql"])
        assert _score_skill_match(facts, cfg) == 1.0

    def test_empty_required_only_nice(self):
        """Empty required, nice_to_have partially matched."""
        cfg = Cfg(my_skills=["python"])
        facts = _facts(required_skills=[], nice_to_have=["python", "kafka", "spark"])
        score = _score_skill_match(facts, cfg)
        assert abs(score - 1/3) < 0.001

    def test_both_empty(self):
        cfg = Cfg()
        facts = _facts(required_skills=[], nice_to_have=[])
        assert _score_skill_match(facts, cfg) == 1.0

    def test_score_capped_at_one(self):
        """Can never exceed 1.0 even if all nice-to-have also match."""
        cfg = Cfg(my_skills=["python", "sql", "tableau", "excel", "r"])
        facts = _facts(
            required_skills=["python", "sql"],
            nice_to_have=["tableau", "excel", "r"],
        )
        assert _score_skill_match(facts, cfg) <= 1.0


# ===========================================================================
# Seniority fit unit tests
# ===========================================================================

class TestSeniorityFit:

    @pytest.mark.parametrize("fact,target,expected", [
        ("mid",     "mid",     1.0),
        ("junior", "mid",     0.6),
        ("senior", "mid",     0.6),
        ("junior", "senior", 0.25),
        ("lead",   "mid",     0.25),
        ("junior", "lead",   0.0),
        ("mid",    "lead",   0.25),
        ("unknown","mid",     0.5),
        ("mid",    "unknown",0.5),
    ])
    def test_seniority_table(self, fact, target, expected):
        cfg = Cfg(target_seniority=target)
        score = _score_seniority_fit({"seniority": fact}, cfg)
        assert score == expected, f"fact={fact} target={target}: expected {expected}, got {score}"


# ===========================================================================
# Location fit unit tests
# ===========================================================================

class TestLocationFit:

    def test_remote_true_overrides_location(self):
        cfg = Cfg()
        score = _score_location_fit({"remote_ok": True}, {"location": "Mars"}, cfg)
        assert score == 1.0

    def test_city_match(self):
        cfg = Cfg(target_city="Bengaluru")
        score = _score_location_fit({"remote_ok": False}, {"location": "Bengaluru, India"}, cfg)
        assert score == 1.0

    def test_city_match_case_insensitive(self):
        cfg = Cfg(target_city="Bengaluru")
        score = _score_location_fit({"remote_ok": False}, {"location": "BENGALURU"}, cfg)
        assert score == 1.0

    def test_empty_location_returns_half(self):
        cfg = Cfg()
        assert _score_location_fit({"remote_ok": None}, {"location": ""}, cfg) == 0.5

    def test_none_location_returns_half(self):
        cfg = Cfg()
        assert _score_location_fit({"remote_ok": None}, {"location": None}, cfg) == 0.5

    def test_mismatch_remote_false(self):
        cfg = Cfg(target_city="Bengaluru")
        score = _score_location_fit({"remote_ok": False}, {"location": "San Francisco"}, cfg)
        assert score == 0.1

    def test_mismatch_remote_none_benefit_of_doubt(self):
        cfg = Cfg(target_city="Bengaluru")
        score = _score_location_fit({"remote_ok": None}, {"location": "London"}, cfg)
        assert score == 0.5


# ===========================================================================
# Recency unit tests
# ===========================================================================

class TestRecency:

    def test_today(self):
        score = _score_recency({"posted_at": datetime.now(timezone.utc).isoformat()})
        assert score >= 0.99

    def test_thirty_days(self):
        stale = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        assert _score_recency({"posted_at": stale}) <= 0.01

    def test_fifteen_days_midpoint(self):
        mid = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
        score = _score_recency({"posted_at": mid})
        assert 0.49 < score < 0.51

    def test_null_posted_at(self):
        assert _score_recency({"posted_at": None}) == 0.5

    def test_missing_key(self):
        assert _score_recency({}) == 0.5

    def test_invalid_string(self):
        assert _score_recency({"posted_at": "not-a-date"}) == 0.5

    def test_future_date_clamps_to_one(self):
        future = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
        assert _score_recency({"posted_at": future}) == 1.0


# ===========================================================================
# build_reason unit tests
# ===========================================================================

class TestBuildReason:

    def test_gap_skills_named(self):
        """Missing required skills appear by name in the reason string."""
        cfg = Cfg(my_skills=["python"])
        facts = _facts(required_skills=["python", "kubernetes", "spark"])
        components = {
            "skill_match": 0.33, "seniority_fit": 1.0,
            "location_fit": 1.0, "recency": 1.0,
        }
        reason = build_reason(components, facts, cfg, _listing())
        assert "kubernetes" in reason
        assert "spark" in reason

    def test_no_gap_when_all_matched(self):
        cfg = Cfg(my_skills=["python", "sql"])
        facts = _facts(required_skills=["python", "sql"])
        components = {
            "skill_match": 1.0, "seniority_fit": 1.0,
            "location_fit": 1.0, "recency": 1.0,
        }
        reason = build_reason(components, facts, cfg, _listing())
        assert "gap" not in reason

    def test_remote_label(self):
        cfg = Cfg()
        facts = _facts(remote_ok=True)
        components = {
            "skill_match": 1.0, "seniority_fit": 1.0,
            "location_fit": 1.0, "recency": 1.0,
        }
        reason = build_reason(components, facts, cfg, _listing())
        assert "remote" in reason.lower()

    def test_null_posted_at_label(self):
        cfg = Cfg()
        facts = _facts()
        components = {
            "skill_match": 1.0, "seniority_fit": 1.0,
            "location_fit": 1.0, "recency": 0.5,
        }
        reason = build_reason(components, facts, cfg, _listing(posted_at=None))
        assert "unknown" in reason

    def test_separator_present(self):
        """Parts are joined with ' · '."""
        cfg = Cfg()
        reason = build_reason(
            {"skill_match": 1.0, "seniority_fit": 1.0, "location_fit": 1.0, "recency": 1.0},
            _facts(),
            cfg,
            _listing(),
        )
        assert " · " in reason