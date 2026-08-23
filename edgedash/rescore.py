"""
Manual re-scoring escape hatch.

Usage:
    python -m edgedash.rescore --all          # clear every score
    python -m edgedash.rescore --id <id>      # clear one listing

Clears: fit_score, fit_reason, fit_components, scored_at.
Never touches the extraction cache — re-scoring costs zero API calls.

Rule 18 says never re-score automatically. This command is intentionally
manual: you run it on purpose after editing weights in config.yaml, then
run the normal cycle to re-rank.
"""

import argparse
import sys

from edgedash import storage
from edgedash.config import Config


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m edgedash.rescore",
        description="Clear stored scores so the next cycle re-scores them.",
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--all",
        action="store_true",
        help="Clear every score in the database.",
    )
    group.add_argument(
        "--id",
        metavar="LISTING_ID",
        help="Clear the score for a single listing by its ID.",
    )

    args = parser.parse_args()

    try:
        config = Config.load()
    except (FileNotFoundError, ImportError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    db_path = config.db_path

    if args.all:
        # Refuse without explicit confirmation — rule 18 spirit.
        print(
            "This will clear ALL scores. "
            "The next cycle will re-score every listing from scratch."
        )
        try:
            answer = input("Type YES to confirm: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            sys.exit(0)

        if answer != "YES":
            print("Aborted — nothing changed.")
            sys.exit(0)

        cleared = storage.clear_scores(db_path)
        print(f"Cleared {cleared} score(s).")

    else:  # --id
        listing_id = args.id
        cleared = storage.clear_scores(db_path, listing_id=listing_id)

        if cleared == 0:
            print(
                f"No listing found with id '{listing_id}'. "
                "Check the ID and try again."
            )
            sys.exit(1)

        print(f"Cleared score for listing '{listing_id}'.")

    print("Run the cycle to re-score: python -m edgedash")


if __name__ == "__main__":
    main()
