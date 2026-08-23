"""
State-driven Orchestrator. Rules 28-33.

Reads state → builds plan → prints plan → executes → writes one summary row.
No fetching, scoring, or analysis logic here.
The Orchestrator coordinates and never does the work.
"""

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from edgedash.agents import Agent, AgentResult, Fetcher, GapAnalyzer, MockFetcher, Scorer
from edgedash import storage
from edgedash import state as state_module
from edgedash import planning as planning_module

if TYPE_CHECKING:
    from edgedash.config import Config


def run_cycle(
    config: "Config",
    forced_agents: list[str] | None = None,
) -> None:
    """
    Execute one cycle. Returns normally on all outcomes including nothing_to_do.
    Exit code 0 in all cases — "nothing to do" is a success (rule 28).
    """
    # ── Registry: add a fourth agent here and nowhere else ───────────────
    agent_registry: dict[str, Agent] = {
        "fetcher":   MockFetcher() if config.use_mock_fetcher else Fetcher(),
        "scorer":    Scorer(),
        "analyser":  GapAnalyzer(),
    }

    cycle_start = datetime.now(tz=timezone.utc)

    print("\n" + "=" * 70)
    print("EDGEDASH CYCLE")
    print("=" * 70)

    # ── Init DB ───────────────────────────────────────────────────────────
    storage.init_db(config.db_path)

    # ── 1. Read state (rule 28) ───────────────────────────────────────────
    sys_state = state_module.read_state(config, now=cycle_start)

    print(f"\n  last_fetch:        {sys_state.last_fetch_at or 'never'}")
    print(f"  hours_since_fetch: {_fmt_hours(sys_state.hours_since_fetch)}")
    print(f"  unscored_count:    {sys_state.unscored_count}")
    print(f"  gaps_stale:        {sys_state.gaps_stale}")
    print(f"  last_verdict:      {sys_state.last_cycle_verdict or 'none'}")

    # ── 2. Build plan (rule 31) ───────────────────────────────────────────
    plan = planning_module.build_plan(sys_state, config)

    # ── Apply --force overrides (operator layer, not planning logic) ──────
    forced_agents = forced_agents or []
    if forced_agents:
        from edgedash.planning import Task, Plan
        new_tasks = []
        for task in plan.tasks:
            if task.agent_name in forced_agents and task.skipped:
                new_tasks.append(Task(
                    agent_name=task.agent_name,
                    goal=task.goal,
                    stop_conditions=task.stop_conditions,
                    reason="forced by operator",
                    skipped=False,
                ))
            else:
                new_tasks.append(task)
        plan = Plan(tasks=new_tasks)

    print()
    print(plan.render())   # printed BEFORE any execution

    # ── 3. nothing_to_do fast-path ────────────────────────────────────────
    tasks_to_run = plan.agents_to_run()

    if not tasks_to_run:
        _write_summary(
            config=config,
            cycle_start=cycle_start,
            plan=plan,
            run_results=[],
            outcome="nothing_to_do",
            forced_agents=forced_agents,
        )
        print("\n  ✓ nothing to do — cycle complete\n" + "=" * 70 + "\n")
        return   # exit 0, no warning

    # ── 4. Execute tasks (rule 32 — one failure does not stop the cycle) ──
    run_results: list[dict] = []
    any_failed = False

    for task in tasks_to_run:
        agent = agent_registry.get(task.agent_name)
        if agent is None:
            # Misconfigured plan — treat as failure, keep going
            print(f"\n  ✗ {task.agent_name}: NOT IN REGISTRY")
            run_results.append({
                "agent":           task.agent_name,
                "status":          "failed",
                "records_touched": 0,
                "notes":           "agent not found in registry",
                "duration_s":      0.0,
            })
            any_failed = True
            continue

        print(f"\n  ▶ {task.agent_name} …")
        task_start = datetime.now(tz=timezone.utc)

        try:
            result: AgentResult = agent.run(
                config,
                storage,
                stop_conditions=task.stop_conditions,
            )
            task_end = datetime.now(tz=timezone.utc)
            duration_s = (task_end - task_start).total_seconds()

            status_icon = "✓" if result.status == "ok" else "✗"
            print(
                f"    {status_icon} {result.records_touched} records"
                f"  {duration_s:.1f}s  {result.notes}"
            )

            if result.status != "ok":
                any_failed = True

            run_results.append({
                "agent":           task.agent_name,
                "status":          result.status,
                "records_touched": result.records_touched,
                "notes":           result.notes,
                "duration_s":      duration_s,
            })

        except Exception as exc:  # rule 32 — log, continue, mark partial
            task_end = datetime.now(tz=timezone.utc)
            duration_s = (task_end - task_start).total_seconds()
            err_msg = f"{type(exc).__name__}: {exc}"
            print(f"    ✗ FAILED  {duration_s:.1f}s  {err_msg}")
            any_failed = True

            run_results.append({
                "agent":           task.agent_name,
                "status":          "failed",
                "records_touched": 0,
                "notes":           err_msg,
                "duration_s":      duration_s,
            })

    # ── 5. One summary row (rule 33) ──────────────────────────────────────
    outcome = "partial" if any_failed else "complete"
    _write_summary(
        config=config,
        cycle_start=cycle_start,
        plan=plan,
        run_results=run_results,
        outcome=outcome,
        forced_agents=forced_agents,
    )

    total_records = sum(r["records_touched"] for r in run_results)
    total_s = (datetime.now(tz=timezone.utc) - cycle_start).total_seconds()
    print(f"\n  outcome: {outcome}  ·  {total_records} records  ·  {total_s:.1f}s")
    print("=" * 70 + "\n")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_hours(h: "float | None") -> str:
    if h is None:
        return "never"
    return f"{h:.1f}h"


def _write_summary(
    config: "Config",
    cycle_start: datetime,
    plan: "planning_module.Plan",
    run_results: list[dict],
    outcome: str,
    forced_agents: list[str] | None = None,
) -> None:
    """
    Write exactly one cycle summary row to cycle_log (rule 33).

    notes JSON schema:
      {
        "outcome": "complete" | "partial" | "nothing_to_do",
        "plan": [{"agent": str, "skipped": bool, "reason": str}, ...],
        "results": [{"agent": str, "status": str, "records": int,
                     "duration_s": float, "notes": str}, ...]
      }
    """
    cycle_end = datetime.now(tz=timezone.utc)

    plan_summary = [
        {
            "agent":   t.agent_name,
            "skipped": t.skipped,
            "reason":  t.reason,
        }
        for t in plan.tasks
    ]

    results_summary = [
        {
            "agent":      r["agent"],
            "status":     r["status"],
            "records":    r["records_touched"],
            "duration_s": round(r["duration_s"], 2),
            "notes":      r["notes"],
        }
        for r in run_results
    ]

    notes_json = json.dumps({
        "outcome":        outcome,
        "forced_agents":  forced_agents or [],
        "plan":           plan_summary,
        "results":        results_summary,
    })

    total_records = sum(r["records_touched"] for r in run_results)

    storage.log_cycle(
        config.db_path,
        agent="orchestrator",
        started_at=cycle_start.isoformat(),
        finished_at=cycle_end.isoformat(),
        records_touched=total_records,
        status=outcome,
        notes=notes_json,
    )
