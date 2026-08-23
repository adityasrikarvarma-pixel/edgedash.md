"""
Deterministic skill canonicalisation. No LLM. No network. Pure functions.

Rule 23: skill names are canonicalised through an explicit alias map in
config.yaml. Never auto-merged by model judgement or string similarity.
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Core public function
# ---------------------------------------------------------------------------

def canonical(raw: str, aliases: dict[str, str]) -> str:
    """
    Normalise a raw skill string to its canonical form.

    Steps (in order):
      1. Lowercase and strip surrounding whitespace.
      2. Strip surrounding punctuation (quotes, brackets, dots, commas…).
      3. Drop parenthetical qualifiers: "kubernetes (eks)" → "kubernetes".
      4. Collapse internal whitespace runs to a single space.
      5. Look up in aliases map (key must already be normalised).
      6. Return the canonical form, or the normalised string if no alias found.

    Args:
        raw:     The skill string as it came out of the extractor.
        aliases: Dict mapping normalised raw forms to canonical names.
                 Loaded from config.yaml skill_aliases. Keys are already
                 lowercase; values are the preferred display form.

    Returns:
        Canonical skill string. Empty string in → empty string out.

    Examples:
        canonical("Kubernetes (EKS)", {"k8s": "kubernetes"}) → "kubernetes"
        canonical("k8s", {"k8s": "kubernetes"})              → "kubernetes"
        canonical("PostgreSQL", {})                          → "postgresql"
    """
    if not raw or not raw.strip():
        return ""

    # 1. Lowercase + strip surrounding whitespace
    s = raw.lower().strip()

    # 2. Drop parenthetical qualifiers FIRST (before edge-stripping mangles the closing paren)
    #    "kubernetes (eks)" → "kubernetes"
    s = re.sub(r"\s*\(.*?\)", "", s)

    # 3. Strip surrounding punctuation (non-word chars at edges)
    s = s.strip("\"'`.,;:!?()[]{}/\\")

    # 4. Collapse internal whitespace (tabs, multiple spaces, etc.)
    s = re.sub(r"\s+", " ", s).strip()

    if not s:
        return ""

    # 5. Apply alias map
    return aliases.get(s, s)


# ---------------------------------------------------------------------------
# Audit CLI  (python -m edgedash.skills --audit)
# ---------------------------------------------------------------------------

def _load_all_skills(db_path: str) -> list[str]:
    """
    Read every required_skills entry from extraction_cache.
    Returns a flat list of raw skill strings (not yet canonicalised).
    Rule 2: storage access only — but extraction_cache has no storage
    wrapper, so we read via the storage module's sqlite path pattern.
    """
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT required_skills, nice_to_have FROM extraction_cache"
        ).fetchall()
    finally:
        conn.close()

    skills: list[str] = []
    for req_json, nth_json in rows:
        try:
            skills.extend(json.loads(req_json or "[]"))
        except (json.JSONDecodeError, TypeError):
            pass
        try:
            skills.extend(json.loads(nth_json or "[]"))
        except (json.JSONDecodeError, TypeError):
            pass
    return skills


def _run_audit(db_path: str, aliases: dict[str, str]) -> None:
    """
    Print the 40 most common raw skill strings, their canonical forms,
    and the singletons (likely typos / junk / full sentences).
    """
    raw_skills = _load_all_skills(db_path)

    if not raw_skills:
        print("No extracted skills found in the database.")
        print("Run a fetch + extraction cycle first.")
        return

    counts: Counter[str] = Counter(s.lower().strip() for s in raw_skills if s.strip())
    total_unique = len(counts)
    total_occurrences = sum(counts.values())

    # --- Top 40 ---
    print(f"\n{'─'*62}")
    print(f"  SKILL AUDIT  ·  {total_unique} unique  ·  {total_occurrences} total occurrences")
    print(f"{'─'*62}")
    print(f"\n  {'COUNT':>5}  {'RAW SKILL':<30}  CANONICAL")
    print(f"  {'─'*5}  {'─'*30}  {'─'*25}")

    for raw, count in counts.most_common(40):
        canon = canonical(raw, aliases)
        marker = "  ←" if canon != raw else ""
        print(f"  {count:>5}  {raw:<30}  {canon}{marker}")

    # --- Singletons ---
    singletons = sorted(s for s, c in counts.items() if c == 1)
    print(f"\n{'─'*62}")
    print(f"  SINGLETONS ({len(singletons)}) — likely typos, junk, or mis-extracted sentences")
    print(f"{'─'*62}")

    if not singletons:
        print("  (none)")
    else:
        for s in singletons:
            canon = canonical(s, aliases)
            marker = f"  → {canon}" if canon != s else ""
            print(f"  {s}{marker}")

    print(f"\n{'─'*62}")
    print(
        f"  To add an alias: edit skill_aliases in config.yaml\n"
        f"  Then re-run:  python -m edgedash.skills --audit"
    )
    print(f"{'─'*62}\n")


def _run_suggest_aliases(db_path: str, aliases: dict[str, str], config) -> None:
    """
    ONE LLM call to propose groupings of unmapped canonical skill strings.
    Prints ready-to-paste YAML. Writes nothing to any file. Read-only.

    Rule 23: these are suggestions for human review only. The model may
    not change the alias map — you paste what you agree with.
    """
    # Import llm here so this module stays importable even without the SDK
    from edgedash import llm

    # ------------------------------------------------------------------
    # 1. Collect canonical strings NOT already covered by the alias map
    # ------------------------------------------------------------------
    raw_skills = _load_all_skills(db_path)

    if not raw_skills:
        print("\nNo extracted skills found in the database.")
        print("Run a fetch + extraction cycle first.\n")
        return

    # Normalise every raw string the same way canonical() does, but
    # WITHOUT applying aliases — we want the pre-alias form
    seen_counts: Counter[str] = Counter(
        canonical(s, {})   # empty aliases = normalise only, no mapping
        for s in raw_skills
        if s.strip()
    )

    # Build the set of forms already covered:
    #   - alias keys  (the variants)   e.g. "k8s"
    #   - alias values (the canonicals) e.g. "kubernetes"
    already_covered: set[str] = set(aliases.keys()) | set(aliases.values())

    unmapped: dict[str, int] = {
        s: c
        for s, c in seen_counts.items()
        if s and s not in already_covered
    }

    if not unmapped:
        print("\nAll skill strings are already covered by your alias map.")
        print("Nothing to suggest.\n")
        return

    # Sort by frequency descending; cap at 80 strings to keep prompt small
    top_unmapped = sorted(unmapped.items(), key=lambda t: t[1], reverse=True)[:80]
    skill_list_text = "\n".join(f"  {s!r}: {c}" for s, c in top_unmapped)

    # ------------------------------------------------------------------
    # 2. ONE LLM call
    # ------------------------------------------------------------------
    prompt = f"""You are helping maintain a skill alias map for a job-search tool.

Below is a list of skill strings extracted from job listings (format: 'string': count).
Identify groups of strings that clearly refer to the SAME underlying skill
(e.g. "postgresql" and "psql" are both "postgres").

Rules you MUST follow:
- Only group strings that are unambiguously the same skill.
- Do NOT merge skills that are distinct (e.g. "javascript" and "node" are different).
- Do NOT merge a specific tool into a broad category (e.g. "pandas" is NOT "python").
- For each group, pick the clearest canonical name (usually the longest or most common).
- If you are not sure two strings are the same skill, use confidence "low".
- A string that is already clear on its own needs no grouping — omit it.

Skill strings and their occurrence counts:
{skill_list_text}

Respond with a JSON object with a single key "suggestions", whose value is a list.
Each list item must have:
  "canonical": string  — the preferred name for the skill group
  "variants":  list of strings — the OTHER strings that should map to this canonical
  "confidence": "high" or "low"
Only include groups with 2 or more strings total (canonical + at least 1 variant).
"""

    SCHEMA = {"suggestions": "array"}

    print("\n⚙  Calling LLM for alias suggestions (one call)…\n")

    try:
        result = llm.complete_json(prompt, SCHEMA, max_retries=1)
    except llm.LLMError as exc:
        print(f"LLM call failed: {exc}", file=sys.stderr)
        sys.exit(1)

    suggestions = result.get("suggestions") or []

    if not suggestions:
        print("The model returned no grouping suggestions.\n")
        return

    # ------------------------------------------------------------------
    # 3. Detect conflicts with existing alias choices
    # ------------------------------------------------------------------
    # A conflict: the proposal groups two strings that already have
    # DIFFERENT canonical values in the existing alias map.
    existing_canonicals: dict[str, str] = {}   # normalised form → canonical
    for k, v in aliases.items():
        existing_canonicals[k] = v
        existing_canonicals[v] = v   # identity: canonical maps to itself

    conflicts: list[dict] = []
    clean: list[dict] = []

    for s in suggestions:
        canon = s.get("canonical", "")
        variants = s.get("variants") or []
        confidence = s.get("confidence", "low")

        all_strings = [canon] + variants
        mapped_to: set[str] = set()
        for string in all_strings:
            if string in existing_canonicals:
                mapped_to.add(existing_canonicals[string])

        if len(mapped_to) > 1:
            # Multiple existing canonicals would be merged — conflict
            conflicts.append(
                {"canonical": canon, "variants": variants,
                 "confidence": confidence, "mapped_to": sorted(mapped_to)}
            )
        else:
            clean.append({"canonical": canon, "variants": variants,
                          "confidence": confidence})

    # ------------------------------------------------------------------
    # 4. Print output
    # ------------------------------------------------------------------
    W = 70  # line width

    print("─" * W)
    print("  ALIAS SUGGESTIONS  —  READ ONLY, nothing has been written")
    print("─" * W)
    print()
    print("  ⚠  WARNING: These are model suggestions, not facts.")
    print("     Merging two distinct skills is worse than leaving them separate.")
    print("     Review every group before pasting. When in doubt, leave it out.")
    print()

    if conflicts:
        print("─" * W)
        print("  ⛔  CONFLICTS WITH YOUR EXISTING ALIAS MAP  —  DO NOT PASTE THESE")
        print("─" * W)
        for c in conflicts:
            merged = ", ".join(c["mapped_to"])
            print(f"\n  # CONFLICT: would merge existing canonicals [{merged}]")
            print(f"  # Proposed group: {c['canonical']!r}  ←  {c['variants']}")
        print()

    if not clean:
        print("No non-conflicting suggestions to show.\n")
        return

    high = [s for s in clean if s["confidence"] == "high"]
    low  = [s for s in clean if s["confidence"] != "high"]

    def _yaml_block(items: list[dict], label: str, comment: str) -> None:
        if not items:
            return
        print(f"  # {label}")
        print(f"  # {comment}")
        for item in items:
            canon = item["canonical"]
            for variant in item["variants"]:
                if variant != canon:
                    print(f"  {variant}: {canon}")
        print()

    print("─" * W)
    print("  READY-TO-PASTE YAML  —  copy into skill_aliases in config.yaml")
    print("─" * W)
    print()

    _yaml_block(
        high,
        "HIGH CONFIDENCE — review before pasting",
        "Model is confident these refer to the same skill",
    )
    _yaml_block(
        low,
        "LOW CONFIDENCE — be careful",
        "Model is uncertain; verify manually before adding",
    )

    print("─" * W)
    print(f"  {len(clean)} suggestion group(s) shown · {len(conflicts)} conflict(s) suppressed")
    print("─" * W)
    print()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m edgedash.skills",
        description="Skill canonicalisation tools. All modes are read-only.",
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--audit",
        action="store_true",
        help="Print the 40 most common raw skill strings and their canonical forms.",
    )
    mode.add_argument(
        "--suggest-aliases",
        action="store_true",
        help=(
            "Ask the LLM to propose alias groupings for unmapped skill strings. "
            "READ-ONLY — prints YAML to stdout, writes nothing."
        ),
    )
    args = parser.parse_args()

    # Import here to avoid circular imports at module level
    from edgedash.config import Config

    try:
        config = Config.load()
    except (FileNotFoundError, ImportError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    aliases = getattr(config, "skill_aliases", {}) or {}

    if args.audit:
        _run_audit(config.db_path, aliases)
    else:
        _run_suggest_aliases(config.db_path, aliases, config)


if __name__ == "__main__":
    main()
