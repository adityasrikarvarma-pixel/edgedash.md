import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional


def _stable_id(source: str, url: str) -> str:
    """Generate stable listing ID from source and URL."""
    combined = f"{source}:{url}".encode("utf-8")
    return hashlib.sha256(combined).hexdigest()[:16]


def init_db(db_path: str) -> None:
    """Initialize database with required tables if they don't exist."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Listings table: job postings with optional fit data
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS listings (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            location TEXT,
            url TEXT NOT NULL,
            description TEXT,
            source TEXT NOT NULL,
            posted_at TEXT,
            fetched_at TEXT NOT NULL,
            fit_score INTEGER,
            fit_reason TEXT,
            fit_components TEXT,
            scored_at TEXT
        )
        """
    )

    # Safely add new columns if they don't exist (for existing databases)
    cursor.execute("PRAGMA table_info(listings)")
    columns = {row[1] for row in cursor.fetchall()}
    if "fit_components" not in columns:
        cursor.execute("ALTER TABLE listings ADD COLUMN fit_components TEXT")
    if "scored_at" not in columns:
        cursor.execute("ALTER TABLE listings ADD COLUMN scored_at TEXT")
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS skill_gaps (
            skill TEXT PRIMARY KEY,
            frequency INTEGER NOT NULL,
            last_seen TEXT NOT NULL
        )
        """
    )

    # Cycle log: audit trail of every agent run
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS cycle_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            records_touched INTEGER NOT NULL,
            status TEXT NOT NULL,
            notes TEXT
        )
        """
    )

    # Extraction cache: keyed on description hash, stores LLM extraction results
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS extraction_cache (
            description_hash TEXT PRIMARY KEY,
            required_skills TEXT NOT NULL,
            nice_to_have TEXT NOT NULL,
            seniority TEXT NOT NULL,
            years_required INTEGER,
            remote_ok INTEGER,
            cached_at TEXT NOT NULL
        )
        """
    )

    conn.commit()
    conn.close()


def upsert_listings(db_path: str, rows: list[dict]) -> int:
    """
    Insert new listings, ignore duplicates by id (stable hash of source+url).
    Returns count of genuinely new rows inserted.
    """
    if not rows:
        return 0

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    now = datetime.utcnow().isoformat()

    # Identify which listings are new by checking existing IDs
    listing_ids = [_stable_id(row["source"], row["url"]) for row in rows]
    cursor.execute(
        f"SELECT id FROM listings WHERE id IN ({','.join('?' * len(listing_ids))})",
        listing_ids,
    )
    existing_ids = {row[0] for row in cursor.fetchall()}

    # Insert all rows; count only the genuinely new ones
    new_count = 0
    for row in rows:
        listing_id = _stable_id(row["source"], row["url"])
        is_new = listing_id not in existing_ids

        cursor.execute(
            """
            INSERT OR IGNORE INTO listings
            (id, title, company, location, url, description, source, posted_at, fetched_at, fit_score, fit_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                listing_id,
                row.get("title", ""),
                row.get("company", ""),
                row.get("location"),
                row["url"],
                row.get("description"),
                row["source"],
                row.get("posted_at"),
                now,
                row.get("fit_score"),
                row.get("fit_reason"),
            ),
        )
        if is_new:
            new_count += 1

    conn.commit()
    conn.close()
    return new_count


def count_unscored(db_path: str) -> int:
    """Count listings with no fit_score yet."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM listings WHERE fit_score IS NULL")
    count = cursor.fetchone()[0]
    conn.close()
    return count


def last_fetch_time(db_path: str) -> Optional[str]:
    """Return ISO string of the most recent fetch timestamp, or None."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(fetched_at) FROM listings")
    result = cursor.fetchone()[0]
    conn.close()
    return result


def log_cycle(
    db_path: str,
    agent: str,
    started_at: str,
    finished_at: str,
    records_touched: int,
    status: str,
    notes: Optional[str] = None,
) -> None:
    """Write a cycle log entry for this agent run."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO cycle_log (agent, started_at, finished_at, records_touched, status, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (agent, started_at, finished_at, records_touched, status, notes),
    )
    conn.commit()
    conn.close()


def get_listings(
    db_path: str, limit: int = 10, min_score: Optional[int] = None
) -> list[dict]:
    """
    Fetch listings, optionally filtered by minimum fit_score.
    Returns list of dicts ordered by fit_score descending, then by fetched_at descending.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if min_score is not None:
        cursor.execute(
            """
            SELECT * FROM listings
            WHERE fit_score IS NOT NULL AND fit_score >= ?
            ORDER BY fit_score DESC, fetched_at DESC
            LIMIT ?
            """,
            (min_score, limit),
        )
    else:
        cursor.execute(
            """
            SELECT * FROM listings
            ORDER BY fetched_at DESC
            LIMIT ?
            """,
            (limit,),
        )

    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_extraction(db_path: str, description_hash: str) -> Optional[dict]:
    """
    Fetch cached extraction by description hash. Returns dict or None.
    Converts numeric fields back to proper types (bool for remote_ok).
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT required_skills, nice_to_have, seniority, years_required, remote_ok
        FROM extraction_cache
        WHERE description_hash = ?
        """,
        (description_hash,),
    )

    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    import json

    return {
        "required_skills": json.loads(row["required_skills"]),
        "nice_to_have": json.loads(row["nice_to_have"]),
        "seniority": row["seniority"],
        "years_required": row["years_required"],
        "remote_ok": bool(row["remote_ok"]) if row["remote_ok"] is not None else None,
    }


def cache_extraction(db_path: str, description_hash: str, extraction: dict) -> None:
    """
    Store extraction result in cache. Converts bool to 0/1 for SQLite.
    """
    import json

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    now = datetime.utcnow().isoformat()

    # Convert bool to 0/1 for SQLite storage
    remote_ok_value = (
        1 if extraction.get("remote_ok") is True else (0 if extraction.get("remote_ok") is False else None)
    )

    cursor.execute(
        """
        INSERT OR REPLACE INTO extraction_cache
        (description_hash, required_skills, nice_to_have, seniority, years_required, remote_ok, cached_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            description_hash,
            json.dumps(extraction.get("required_skills") or []),
            json.dumps(extraction.get("nice_to_have") or []),
            extraction.get("seniority", "unknown"),
            extraction.get("years_required"),
            remote_ok_value,
            now,
        ),
    )
    conn.commit()
    conn.close()


def update_score(
    db_path: str, listing_id: str, score: int, reason: str, components: dict
) -> None:
    """
    Update a listing with its final score, reason, and components breakdown.
    """
    import json

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    now = datetime.utcnow().isoformat()

    cursor.execute(
        """
        UPDATE listings
        SET fit_score = ?, fit_reason = ?, fit_components = ?, scored_at = ?
        WHERE id = ?
        """,
        (
            score,
            reason,
            json.dumps(components),
            now,
            listing_id,
        ),
    )
    conn.commit()
    conn.close()


def get_unscored_listings(db_path: str, limit: int = 25) -> list[dict]:
    """
    Fetch listings where fit_score IS NULL, oldest-fetched first.
    Used by the Scorer agent to find work to do.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM listings
        WHERE fit_score IS NULL
        ORDER BY fetched_at ASC
        LIMIT ?
        """,
        (limit,),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows

def get_scored_listings(db_path: str) -> list[dict]:
    """
    Fetch ALL listings that have a fit_score (IS NOT NULL).
    Returns every scored row — no limit — ordered by fit_score DESC.
    Used by GapAnalyzer to analyse the full scored corpus.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM listings
        WHERE fit_score IS NOT NULL
        ORDER BY fit_score DESC
        """
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def clear_scores(db_path: str, listing_id: Optional[str] = None) -> int:
    """
    Clear fit_score, fit_reason, fit_components, and scored_at.
    Never touches the extraction cache.

    Args:
        listing_id: if given, clear only that row; if None, clear all rows.

    Returns:
        Number of rows updated.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    if listing_id is not None:
        cursor.execute(
            """
            UPDATE listings
            SET fit_score = NULL, fit_reason = NULL,
                fit_components = NULL, scored_at = NULL
            WHERE id = ?
            """,
            (listing_id,),
        )
    else:
        cursor.execute(
            """
            UPDATE listings
            SET fit_score = NULL, fit_reason = NULL,
                fit_components = NULL, scored_at = NULL
            """
        )
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected


def _migrate_gap_snapshots(cursor) -> None:
    """
    Create the gap_snapshots table if it does not exist.
    The old skill_gaps table (skill TEXT PRIMARY KEY) is left untouched
    for backward compatibility — it is simply no longer written to.
    gap_snapshots is append-only: every run gets its own rows (rule 25).
    """
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS gap_snapshots (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id       TEXT    NOT NULL,
            computed_at  TEXT    NOT NULL,
            skill        TEXT    NOT NULL,
            listings_blocked INTEGER NOT NULL,
            opportunity_cost REAL NOT NULL,
            mean_score   REAL    NOT NULL,
            top_score    INTEGER NOT NULL,
            also_nice_to_have INTEGER NOT NULL DEFAULT 0,
            example_ids  TEXT    NOT NULL,
            low_confidence INTEGER NOT NULL DEFAULT 0
        )
        """
    )


def write_gap_snapshot(db_path: str, run_id: str, computed_at: str, gaps: list[dict]) -> int:
    """
    Append one snapshot's worth of gap rows. Never overwrites previous runs.

    Each dict in gaps must have:
        skill, listings_blocked, opportunity_cost, mean_score, top_score,
        also_nice_to_have, example_ids (JSON string), low_confidence (0/1)

    Returns number of rows written.
    """
    if not gaps:
        return 0

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    _migrate_gap_snapshots(cursor)

    cursor.executemany(
        """
        INSERT INTO gap_snapshots
            (run_id, computed_at, skill, listings_blocked, opportunity_cost,
             mean_score, top_score, also_nice_to_have, example_ids, low_confidence)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                run_id,
                computed_at,
                g["skill"],
                g["listings_blocked"],
                g["opportunity_cost"],
                g["mean_score"],
                g["top_score"],
                g["also_nice_to_have"],
                g["example_ids"],        # already JSON-encoded string
                g["low_confidence"],
            )
            for g in gaps
        ],
    )
    conn.commit()
    conn.close()
    return len(gaps)


def get_latest_gap_snapshot(db_path: str) -> list[dict]:
    """
    Return all rows from the most recent run_id, ordered by opportunity_cost DESC.
    Returns [] if the table doesn't exist yet or is empty.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Table may not exist yet on a fresh DB
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='gap_snapshots'"
    )
    if not cursor.fetchone():
        conn.close()
        return []

    cursor.execute(
        "SELECT MAX(run_id) FROM gap_snapshots"
    )
    row = cursor.fetchone()
    latest_run_id = row[0] if row else None
    if not latest_run_id:
        conn.close()
        return []

    cursor.execute(
        """
        SELECT * FROM gap_snapshots
        WHERE run_id = ?
        ORDER BY opportunity_cost DESC
        """,
        (latest_run_id,),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def get_all_gap_snapshots(db_path: str) -> list[dict]:
    """
    Return every row from gap_snapshots, ordered by computed_at ASC then
    opportunity_cost DESC. Used by the trend reporter.
    Returns [] if the table doesn't exist yet or is empty.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='gap_snapshots'"
    )
    if not cursor.fetchone():
        conn.close()
        return []

    cursor.execute(
        """
        SELECT * FROM gap_snapshots
        ORDER BY computed_at ASC, opportunity_cost DESC
        """
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def last_scored_at(db_path: str) -> Optional[str]:
    """Return ISO string of the most recent scored_at across all listings, or None."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(scored_at) FROM listings")
    result = cursor.fetchone()[0]
    conn.close()
    return result


def last_gap_computed_at(db_path: str) -> Optional[str]:
    """
    Return ISO string of the most recent computed_at in gap_snapshots, or None.
    Returns None if the table doesn't exist yet.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='gap_snapshots'"
    )
    if not cursor.fetchone():
        conn.close()
        return None
    cursor.execute("SELECT MAX(computed_at) FROM gap_snapshots")
    result = cursor.fetchone()[0]
    conn.close()
    return result


def last_cycle_summary(db_path: str) -> tuple[Optional[str], Optional[str]]:
    """
    Return (status, finished_at) of the most recent cycle_log row, or (None, None).
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT status, finished_at FROM cycle_log ORDER BY finished_at DESC LIMIT 1"
    )
    row = cursor.fetchone()
    conn.close()
    if row:
        return row[0], row[1]
    return None, None
