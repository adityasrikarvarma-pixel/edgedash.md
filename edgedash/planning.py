"""
Orchestrator planning. Deterministic — no LLM, no I/O, no clock.

`build_plan` is a pure function of (SystemState, Config).
The same inputs always produce the same Plan.
This means the entire decision logic of the system is unit-testable
without touching a database or patching time.
"""

from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

from edgedash.state import SystemState

if TYPE_CHECKING:
    from edgedash.config import Config


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StopConditions:
    """Limits the Orchestrator passes to an agent. The agent must not exceed them."""
    max_items: Optional[int] = None
    max_seconds: Optional[int] = None
    max_pages: Optional[int] = None

    def render(self) -> str:
        parts = []
        if self.max_items is not None:
            parts.append(f"max_items={self.max_items}")
        if self.max_seconds is not None:
            parts.append(f"max_seconds={self.max_seconds}")
        if self.max_pages is not None:
            parts.append(f"max_pages={self.max_pages}")
        return ", ".join(parts) if parts else "none"


@dataclass(frozen=True)
class Task:
    """
    One delegation unit in a Plan.

    skipped=True means the agent was considered but has no work.
    Skipped tasks still appear in the Plan (Rule 31).
    """
    agent_name: str
    goal: str
    stop_conditions: StopConditions
    reason: str          # the state value that caused this decision
    skipped: bool = False


@dataclass
class Plan:
    """Ordered list of Tasks, including skipped ones."""
    tasks: list[Task] = field(default_factory=list)

    def render(self) -> str:
        """
        Compact one-line-per-agent plan string.
        Skipped agents are shown with a ✗ prefix.
        Running agents are shown with a ▶ prefix.
        """
        lines = ["PLAN:"]
        for t in self.tasks:
            if t.skipped:
                lines.append(
                    f"  ✗ {t.agent_name:<12}  skipped  — {t.reason}"
                )
            else:
                sc = t.stop_conditions.render()
                lines.append(
                    f"  ▶ {t.agent_name:<12}  {t.goal}  [{sc}]  — {t.reason}"
                )
        return "\n".join(lines)

    def agents_to_run(self) -> list[Task]:
        """Return only the non-skipped tasks, in order."""
        return [t for t in self.tasks if not t.skipped]


# ---------------------------------------------------------------------------
# The planner
# ---------------------------------------------------------------------------


def build_plan(state: SystemState, config: "Config") -> Plan:
    """
    Decide which agents to run and build a Plan.

    Pure function — no I/O, no clock, no randomness.
    Every threshold comes from config; every decision comes from state.

    Decision rules:
      fetch:   run if hours_since_fetch is None (never fetched)
               OR hours_since_fetch >= config.fetch_interval_hours
      score:   run if unscored_count > 0
      analyse: run if gaps_stale is True OR gaps_computed_at is None

    Skipped agents appear in the Plan with skipped=True and a reason
    string so the log explains the quiet morning (Rule 31).
    """
    tasks: list[Task] = []

    # ── Fetch ──────────────────────────────────────────────────────────────
    if state.hours_since_fetch is None:
        fetch_reason = "hours_since_fetch=never (first run)"
        fetch_skipped = False
    elif state.hours_since_fetch >= config.fetch_interval_hours:
        h = f"{state.hours_since_fetch:.1f}"
        fetch_reason = (
            f"hours_since_fetch={h} >= fetch_interval_hours={config.fetch_interval_hours}"
        )
        fetch_skipped = False
    else:
        h = f"{state.hours_since_fetch:.1f}"
        fetch_reason = (
            f"skipped: hours_since_fetch={h} < fetch_interval_hours={config.fetch_interval_hours}"
        )
        fetch_skipped = True

    tasks.append(Task(
        agent_name="fetcher",
        goal="fetch new job listings",
        stop_conditions=StopConditions(
            max_pages=config.fetch_max_pages,
            max_items=config.fetch_max_listings,
        ),
        reason=fetch_reason,
        skipped=fetch_skipped,
    ))

    # ── Score ──────────────────────────────────────────────────────────────
    if state.unscored_count > 0:
        score_reason = f"unscored_count={state.unscored_count}"
        score_skipped = False
    else:
        score_reason = "skipped: unscored_count=0"
        score_skipped = True

    tasks.append(Task(
        agent_name="scorer",
        goal="score unscored listings",
        stop_conditions=StopConditions(
            max_items=config.score_batch_size,
            max_seconds=config.score_max_seconds,
        ),
        reason=score_reason,
        skipped=score_skipped,
    ))

    # ── Analyse ────────────────────────────────────────────────────────────
    if state.gaps_computed_at is None:
        analyse_reason = "gaps_computed_at=null (never run)"
        analyse_skipped = False
    elif state.gaps_stale:
        analyse_reason = "gaps_stale=True (scores newer than last gap run)"
        analyse_skipped = False
    else:
        analyse_reason = "skipped: gaps_stale=False"
        analyse_skipped = True

    tasks.append(Task(
        agent_name="analyser",
        goal="compute gap snapshot",
        stop_conditions=StopConditions(
            max_seconds=config.analyse_max_seconds,
        ),
        reason=analyse_reason,
        skipped=analyse_skipped,
    ))

    return Plan(tasks=tasks)
