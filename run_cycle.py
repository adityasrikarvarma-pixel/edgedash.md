#!/usr/bin/env python3
"""EdgeDash: Run one full cycle. Entry point.

Flags
-----
--dry-run
    Read state, build and print the plan, then exit without executing
    anything. No writes, no API calls. Exit code 0.

--force <agent>  (repeatable)
    Add the named agent to the plan even if state says skip it.
    Reason is set to "forced by operator".
    Prints a warning that the plan was manually overridden.
    Recorded in the cycle summary row.

--explain
    Print the full SystemState with every value and the decision it drove,
    then proceed normally. Answers "why did it skip that?"
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

from edgedash.config import Config
from edgedash.orchestrator import run_cycle
from edgedash import state as state_module
from edgedash import planning as planning_module
from edgedash import storage


def _explain(sys_state, plan: "planning_module.Plan") -> None:
    """
    Print SystemState values alongside the decision each one drove.
    Format: value → decision
    """
    print("\n  EXPLAIN:")
    print(f"  {'─'*64}")

    # Fetch decision
    hsfetch = (
        f"{sys_state.hours_since_fetch:.1f}h"
        if sys_state.hours_since_fetch is not None
        else "never"
    )
    fetch_task = next(t for t in plan.tasks if t.agent_name == "fetcher")
    fetch_verdict = "RUN" if not fetch_task.skipped else "skip"
    print(f"  hours_since_fetch = {hsfetch:<12}  →  fetcher: {fetch_verdict}")
    print(f"    last_fetch_at   = {sys_state.last_fetch_at or 'never'}")

    # Score decision
    score_task = next(t for t in plan.tasks if t.agent_name == "scorer")
    score_verdict = "RUN" if not score_task.skipped else "skip"
    print(f"  unscored_count    = {sys_state.unscored_count:<12}  →  scorer:  {score_verdict}")

    # Analyse decision
    analyse_task = next(t for t in plan.tasks if t.agent_name == "analyser")
    analyse_verdict = "RUN" if not analyse_task.skipped else "skip"
    print(f"  gaps_stale        = {str(sys_state.gaps_stale):<12}  →  analyser: {analyse_verdict}")
    print(f"    gaps_computed_at= {sys_state.gaps_computed_at or 'never'}")

    # Last cycle context
    print(f"  last_cycle_verdict= {sys_state.last_cycle_verdict or 'none'}")
    print(f"  last_cycle_at     = {sys_state.last_cycle_at or 'never'}")
    print(f"  {'─'*64}")


def _apply_force(
    plan: "planning_module.Plan",
    forced_agents: list[str],
    config: "Config",
) -> "planning_module.Plan":
    """
    Return a new Plan with the named agents un-skipped.

    build_plan stays a pure function. --force adds to its output here,
    in the operator layer, not inside the planning rules.

    For each forced agent that is currently skipped, replace its Task
    with an identical one where skipped=False and reason is set to
    "forced by operator".  The stop_conditions from the original task
    are preserved so limits are still respected.
    """
    new_tasks = []
    for task in plan.tasks:
        if task.agent_name in forced_agents and task.skipped:
            # Replace with forced version — keep stop_conditions intact
            new_tasks.append(planning_module.Task(
                agent_name=task.agent_name,
                goal=task.goal,
                stop_conditions=task.stop_conditions,
                reason="forced by operator",
                skipped=False,
            ))
        else:
            new_tasks.append(task)

    return planning_module.Plan(tasks=new_tasks)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="run_cycle.py",
        description="EdgeDash: run one cycle.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read state, print the plan, exit without executing. No writes.",
    )
    parser.add_argument(
        "--force",
        metavar="AGENT",
        action="append",
        default=[],
        dest="forced_agents",
        help=(
            "Force an agent to run even if state says skip. "
            "Repeatable: --force fetcher --force scorer"
        ),
    )
    parser.add_argument(
        "--explain",
        action="store_true",
        help="Print every SystemState value and the decision it drove.",
    )

    args = parser.parse_args()

    try:
        config = Config.load()
    except (FileNotFoundError, ImportError) as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1

    print(f"\n✓ Config loaded: {config.target_role} in {config.target_city}")

    # ── Validate --force agent names against known agents ─────────────────
    known_agents = {"fetcher", "scorer", "analyser"}
    unknown = set(args.forced_agents) - known_agents
    if unknown:
        print(
            f"❌ Unknown agent(s) in --force: {', '.join(sorted(unknown))}\n"
            f"   Known agents: {', '.join(sorted(known_agents))}",
            file=sys.stderr,
        )
        return 1

    try:
        # ── For --dry-run, --explain, --force: we need state+plan here ────
        if args.dry_run or args.explain or args.forced_agents:
            storage.init_db(config.db_path)
            now = datetime.now(tz=timezone.utc)
            sys_state = state_module.read_state(config, now=now)
            plan = planning_module.build_plan(sys_state, config)

            # Apply --force before --explain so explain shows the final plan
            if args.forced_agents:
                plan = _apply_force(plan, args.forced_agents, config)

            if args.explain:
                _explain(sys_state, plan)

            if args.dry_run:
                print()
                print(plan.render())
                print("\n  [dry-run] no execution — exiting\n")
                return 0  # exit 0, no warning, no writes

            # --force only (or --force + --explain): print the override warning
            if args.forced_agents:
                print(
                    f"\n  ⚠  PLAN OVERRIDE: {', '.join(args.forced_agents)} "
                    f"forced by operator"
                )

        # ── Normal execution (with optional forced_agents list) ────────────
        run_cycle(
            config,
            forced_agents=args.forced_agents if args.forced_agents else [],
        )
        return 0

    except Exception as e:
        print(f"❌ Unexpected error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
