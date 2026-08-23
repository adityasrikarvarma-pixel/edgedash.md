"""
Tests for build_plan() — pure function, no DB, no clock.

Four cases:
  1. everything_stale  — all three agents run
  2. nothing_to_do     — all three agents skipped
  3. only_unscored     — only scorer runs (fetch recent, gaps fresh)
  4. gaps_stale_no_unscored — only analyser runs
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

import pytest

from edgedash.planning import build_plan, Plan, Task
from edgedash.state import SystemState


# ---------------------------------------------------------------------------
# Minimal Config stub — only the fields build_plan reads
# ---------------------------------------------------------------------------

@dataclass
class _Config:
    fetch_interval_hours: int = 6
    fetch_max_pages: int = 5
    fetch_max_listings: int = 200
    score_batch_size: int = 25
    score_max_seconds: int = 300
    analyse_max_seconds: int = 120


_CFG = _Config()

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
RECENT = NOW - timedelta(hours=2)    # 2 h ago — within fetch_interval
STALE  = NOW - timedelta(hours=10)   # 10 h ago — beyond fetch_interval


def _state(
    hours_since_fetch: Optional[float],
    unscored_count: int,
    gaps_stale: bool,
    gaps_computed_at: Optional[datetime] = NOW,
) -> SystemState:
    return SystemState(
        last_fetch_at=None,
        hours_since_fetch=hours_since_fetch,
        unscored_count=unscored_count,
        gaps_computed_at=gaps_computed_at,
        gaps_stale=gaps_stale,
        last_cycle_verdict=None,
        last_cycle_at=None,
    )


# ---------------------------------------------------------------------------
# Helper assertions
# ---------------------------------------------------------------------------

def _task(plan: Plan, agent: str) -> Task:
    for t in plan.tasks:
        if t.agent_name == agent:
            return t
    raise AssertionError(f"No task for agent {agent!r} in plan")


def _runs(plan: Plan, agent: str) -> bool:
    return not _task(plan, agent).skipped


def _skipped(plan: Plan, agent: str) -> bool:
    return _task(plan, agent).skipped


# ---------------------------------------------------------------------------
# Case 1: everything stale — fetch overdue, unscored listings, gaps stale
# ---------------------------------------------------------------------------

class TestEverythingStale:
    def setup_method(self):
        self.state = _state(hours_since_fetch=10.0, unscored_count=41, gaps_stale=True)
        self.plan = build_plan(self.state, _CFG)

    def test_fetcher_runs(self):
        assert _runs(self.plan, "fetcher")

    def test_scorer_runs(self):
        assert _runs(self.plan, "scorer")

    def test_analyser_runs(self):
        assert _runs(self.plan, "analyser")

    def test_plan_has_three_tasks(self):
        assert len(self.plan.tasks) == 3

    def test_fetcher_reason_contains_hours(self):
        assert "hours_since_fetch" in _task(self.plan, "fetcher").reason

    def test_scorer_reason_contains_count(self):
        assert "unscored_count=41" in _task(self.plan, "scorer").reason

    def test_stop_conditions_set(self):
        sc = _task(self.plan, "scorer").stop_conditions
        assert sc.max_items == _CFG.score_batch_size
        assert sc.max_seconds == _CFG.score_max_seconds


# ---------------------------------------------------------------------------
# Case 2: nothing to do — fetch recent, nothing unscored, gaps fresh
# ---------------------------------------------------------------------------

class TestNothingToDo:
    def setup_method(self):
        self.state = _state(hours_since_fetch=2.0, unscored_count=0, gaps_stale=False)
        self.plan = build_plan(self.state, _CFG)

    def test_fetcher_skipped(self):
        assert _skipped(self.plan, "fetcher")

    def test_scorer_skipped(self):
        assert _skipped(self.plan, "scorer")

    def test_analyser_skipped(self):
        assert _skipped(self.plan, "analyser")

    def test_all_three_still_in_plan(self):
        # Skipped agents must appear in the plan (Rule 31)
        assert len(self.plan.tasks) == 3

    def test_skipped_reasons_are_informative(self):
        assert "skipped" in _task(self.plan, "fetcher").reason
        assert "skipped" in _task(self.plan, "scorer").reason
        assert "skipped" in _task(self.plan, "analyser").reason

    def test_render_contains_all_agents(self):
        rendered = self.plan.render()
        assert "fetcher" in rendered
        assert "scorer" in rendered
        assert "analyser" in rendered

    def test_render_shows_skipped_marker(self):
        rendered = self.plan.render()
        assert "✗" in rendered

    def test_agents_to_run_is_empty(self):
        assert self.plan.agents_to_run() == []


# ---------------------------------------------------------------------------
# Case 3: only unscored — fetch recent, gaps fresh, but unscored > 0
# ---------------------------------------------------------------------------

class TestOnlyUnscored:
    def setup_method(self):
        self.state = _state(hours_since_fetch=1.0, unscored_count=15, gaps_stale=False)
        self.plan = build_plan(self.state, _CFG)

    def test_fetcher_skipped(self):
        assert _skipped(self.plan, "fetcher")

    def test_scorer_runs(self):
        assert _runs(self.plan, "scorer")

    def test_analyser_skipped(self):
        assert _skipped(self.plan, "analyser")

    def test_scorer_reason(self):
        assert "unscored_count=15" in _task(self.plan, "scorer").reason

    def test_only_scorer_in_agents_to_run(self):
        running = [t.agent_name for t in self.plan.agents_to_run()]
        assert running == ["scorer"]


# ---------------------------------------------------------------------------
# Case 4: gaps stale, nothing unscored — only analyser runs
# ---------------------------------------------------------------------------

class TestGapsStaleNoUnscored:
    def setup_method(self):
        self.state = _state(hours_since_fetch=1.0, unscored_count=0, gaps_stale=True)
        self.plan = build_plan(self.state, _CFG)

    def test_fetcher_skipped(self):
        assert _skipped(self.plan, "fetcher")

    def test_scorer_skipped(self):
        assert _skipped(self.plan, "scorer")

    def test_analyser_runs(self):
        assert _runs(self.plan, "analyser")

    def test_only_analyser_in_agents_to_run(self):
        running = [t.agent_name for t in self.plan.agents_to_run()]
        assert running == ["analyser"]

    def test_analyser_stop_conditions(self):
        sc = _task(self.plan, "analyser").stop_conditions
        assert sc.max_seconds == _CFG.analyse_max_seconds
        assert sc.max_items is None


# ---------------------------------------------------------------------------
# First-run edge case: never fetched (hours_since_fetch is None)
# ---------------------------------------------------------------------------

class TestNeverFetched:
    def setup_method(self):
        self.state = _state(
            hours_since_fetch=None,
            unscored_count=0,
            gaps_stale=True,
            gaps_computed_at=None,
        )
        self.plan = build_plan(self.state, _CFG)

    def test_fetcher_runs_on_first_run(self):
        assert _runs(self.plan, "fetcher")

    def test_fetcher_reason_says_never(self):
        assert "never" in _task(self.plan, "fetcher").reason

    def test_analyser_runs_when_gaps_null(self):
        assert _runs(self.plan, "analyser")
