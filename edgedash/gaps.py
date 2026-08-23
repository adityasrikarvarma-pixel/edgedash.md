"""
Gap report CLI.

    python -m edgedash.gaps            # latest snapshot as a readable table
    python -m edgedash.gaps --trend    # compare earliest vs latest snapshot
"""

import argparse
import json
import sys
from collections import defaultdict

from edgedash import storage
from edgedash.config import Config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bar(value: float, max_value: float, width: int = 20) -> str:
    """ASCII bar proportional to value/max_value."""
    if max_value <= 0:
        return " " * width
    filled = round(value / max_value * width)
    return "█" * filled + "░" * (width - filled)


def _load_config() -> Config:
    try:
        return Config.load()
    except (FileNotFoundError, ImportError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Latest snapshot table
# ---------------------------------------------------------------------------

def _print_latest(db_path: str) -> None:
    rows = storage.get_latest_gap_snapshot(db_path)

    if not rows:
        print("\n  No gap snapshots found.")
        print("  Run the cycle first:  python -m edgedash\n")
        return

    computed_at = rows[0]["computed_at"][:16].replace("T", " ")
    total_listings = sum(r["listings_blocked"] for r in rows)

    print()
    print(f"  SKILL GAP REPORT  ·  {computed_at}  ·  sample: {total_listings} blocking instances")
    print(f"  {'─'*78}")
    print(
        f"  {'#':>2}  {'SKILL':<22}  {'BLOCKED':>7}  {'COST':>6}  "
        f"{'MEAN':>5}  {'TOP':>4}  {'NTH':>4}  BAR"
    )
    print(f"  {'─'*78}")

    max_cost = rows[0]["opportunity_cost"] if rows else 1.0

    for i, row in enumerate(rows, 1):
        flag = " ⚠ low" if row["low_confidence"] else ""
        bar  = _bar(row["opportunity_cost"], max_cost)
        nth  = row["also_nice_to_have"]
        nth_str = str(nth) if nth else "  -"

        # Rule 27: sample size always shown
        n_label = f"n={row['listings_blocked']}"

        print(
            f"  {i:>2}  {row['skill']:<22}  {n_label:>7}  "
            f"{row['opportunity_cost']:>6.2f}  "
            f"{row['mean_score']:>5.1f}  "
            f"{row['top_score']:>4}  "
            f"{nth_str:>4}  "
            f"{bar}{flag}"
        )

    print(f"  {'─'*78}")
    print(
        "  Columns: BLOCKED=n listings requiring skill  "
        "COST=Σ(score/100)  MEAN=avg score"
    )
    print("  NTH=count where skill also appears as nice-to-have (not mixed in)")
    print("  ⚠ low = fewer than 3 listings (low confidence, rule 27)")
    print()
    print("  Drill into a gap — list the blocking listings:")
    if rows:
        eg = json.loads(rows[0]["example_ids"])
        print(f"    Top gap example IDs: {', '.join(eg)}")
    print()


# ---------------------------------------------------------------------------
# Trend report
# ---------------------------------------------------------------------------

def _print_trend(db_path: str) -> None:
    all_rows = storage.get_all_gap_snapshots(db_path)

    if not all_rows:
        print("\n  No gap snapshots found. Run the cycle first.\n")
        return

    # Group by run_id, keeping insertion order (rows already sorted by computed_at ASC)
    runs: dict[str, list[dict]] = {}
    for row in all_rows:
        runs.setdefault(row["run_id"], []).append(row)

    run_ids = list(runs.keys())

    if len(run_ids) == 1:
        snap_date = all_rows[0]["computed_at"][:10]
        print()
        print(f"  TREND REPORT  ·  only 1 snapshot ({snap_date})")
        print()
        print("  Trend requires at least 2 snapshots.")
        days_needed = 2 - len(run_ids)
        print(
            f"  Run the cycle {days_needed} more time(s) on a different day "
            f"to see movement."
        )
        print("  (Printing today's ranking instead.)\n")
        _print_latest(db_path)
        return

    earliest_id = run_ids[0]
    latest_id   = run_ids[-1]
    earliest    = {r["skill"]: r for r in runs[earliest_id]}
    latest      = {r["skill"]: r for r in runs[latest_id]}

    earliest_date = runs[earliest_id][0]["computed_at"][:10]
    latest_date   = runs[latest_id][0]["computed_at"][:10]
    window_days   = len(run_ids)

    print()
    print(
        f"  TREND REPORT  ·  {earliest_date} → {latest_date}"
        f"  ·  {window_days} snapshots"
    )
    print(f"  {'─'*72}")
    print(
        f"  {'#':>2}  {'SKILL':<22}  {'COST NOW':>9}  "
        f"{'CHANGE':>8}  {'%':>6}  NOTE"
    )
    print(f"  {'─'*72}")

    latest_skills = [r["skill"] for r in runs[latest_id]]
    new_skills    = set(latest_skills) - set(earliest.keys())

    for i, skill in enumerate(latest_skills, 1):
        now_cost = latest[skill]["opportunity_cost"]

        if skill in new_skills:
            note = "NEW"
            change_str = "      —"
            pct_str    = "     —"
        else:
            then_cost  = earliest[skill]["opportunity_cost"]
            delta      = now_cost - then_cost
            pct        = (delta / then_cost * 100) if then_cost else 0.0
            sign       = "+" if delta >= 0 else ""
            change_str = f"{sign}{delta:+.2f}"
            pct_str    = f"{sign}{pct:.0f}%"
            note       = "▲" if delta > 0.05 else ("▼" if delta < -0.05 else "~")

        print(
            f"  {i:>2}  {skill:<22}  {now_cost:>9.2f}  "
            f"{change_str:>8}  {pct_str:>6}  {note}"
        )

    # Skills that dropped out of top 10
    dropped = set(earliest.keys()) - set(latest_skills)
    if dropped:
        print(f"  {'─'*72}")
        print(f"  DROPPED OUT of top 10: {', '.join(sorted(dropped))}")

    print(f"  {'─'*72}")
    print(f"  Comparing {earliest_date} (earliest) to {latest_date} (latest)\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m edgedash.gaps",
        description="Print skill gap report from latest snapshot.",
    )
    parser.add_argument(
        "--trend",
        action="store_true",
        help="Compare earliest vs latest snapshot to show movement over time.",
    )
    args = parser.parse_args()

    config  = _load_config()
    db_path = config.db_path

    if args.trend:
        _print_trend(db_path)
    else:
        _print_latest(db_path)


if __name__ == "__main__":
    main()
