"""Extract job facts from listings using LLM. Rule 16: no score field, ever."""

import hashlib
from typing import Any, Optional

from edgedash import llm, storage


# Exact schema for extraction. Rule 16: no score field.
# Arrays and nullable fields use "array?" / "number?" / "boolean?" to signal
# that null is acceptable — the extractor normalises nulls to safe defaults.
EXTRACTION_SCHEMA = {
    "required_skills": "array",  # Skills explicitly required
    "nice_to_have": "array",     # Preferred but not required (may be null from model)
    "seniority": "string",       # One of: junior, mid, senior, lead, unknown
    "years_required": "number",  # Years of experience required, or null
    "remote_ok": "boolean",      # true/false/null (if not stated)
}


def _description_hash(description: Optional[str]) -> str:
    """Generate stable hash of job description for cache key."""
    if not description:
        description = ""
    return hashlib.sha256(description.encode("utf-8")).hexdigest()[:16]


def _clean_description(description: Optional[str]) -> str:
    """
    Strip HTML tags and collapse whitespace before sending to the model.
    Keeps the text content; removes all markup. Truncates at 3000 chars
    so llama3.2 on CPU doesn't time out on massive listings.
    """
    if not description:
        return ""
    import re
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", " ", description)
    # Decode common HTML entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace(
        "&gt;", ">").replace("&nbsp;", " ").replace("&#39;", "'")
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    # Truncate — 3000 chars is enough for skill extraction
    return text[:3000]


def _normalize_skills(skills: list[str]) -> list[str]:
    """Convert all skills to lowercase for consistent matching."""
    return [s.lower() if isinstance(s, str) else "" for s in skills]


def extract(listing: dict, db_path: str) -> dict:
    """
    Extract job facts from a listing. Cache keyed on description hash.
    On cache hit, return instantly. On miss, call LLM once, store, return.
    
    Args:
        listing: dict from get_listings() with keys: title, description, etc.
        db_path: path to database for cache storage
        
    Returns:
        dict with keys: required_skills, nice_to_have, seniority, years_required, remote_ok
        Skills are normalized to lowercase.
    """
    description = listing.get("description", "")
    desc_hash = _description_hash(description)

    # Rule 2: Check cache FIRST. No model call on hit.
    cached = storage.get_extraction(db_path, desc_hash)
    if cached is not None:
        return cached

    # Clean description for the model — strip HTML, truncate
    clean_desc = _clean_description(description)

    # Cache miss: call LLM once with no retry (only first attempt)
    prompt = f"""Extract job facts from the following listing. Only report what the listing explicitly states:
- Do not infer, guess, or evaluate
- Do not mention any candidate profile
- If something is not stated, use null or an empty list
- You are reading a document, nothing more

Listing:
{clean_desc}

Return JSON with exactly these fields:
- required_skills: list of skills the role requires (all lowercase)
- nice_to_have: list of preferred skills (all lowercase)
- seniority: one of "junior", "mid", "senior", "lead", "unknown"
- years_required: years of experience required, or null if not stated
- remote_ok: true if remote is allowed, false if on-site only, null if not stated
"""

    try:
        result = llm.complete_json(prompt, EXTRACTION_SCHEMA, max_retries=1)
    except llm.LLMError as e:
        # On model failure, return conservative defaults (empty/unknown)
        result = {
            "required_skills": [],
            "nice_to_have": [],
            "seniority": "unknown",
            "years_required": None,
            "remote_ok": None,
        }
        print(f"⚠ Extraction failed for listing {listing.get('id', '?')}: {e}")

    # Normalize skills to lowercase
    if isinstance(result.get("required_skills"), list):
        result["required_skills"] = _normalize_skills(result["required_skills"])
    else:
        result["required_skills"] = []

    if isinstance(result.get("nice_to_have"), list):
        result["nice_to_have"] = _normalize_skills(result["nice_to_have"])
    else:
        result["nice_to_have"] = []

    # Validate seniority field
    valid_seniority = {"junior", "mid", "senior", "lead", "unknown"}
    if result.get("seniority") not in valid_seniority:
        result["seniority"] = "unknown"

    # Validate years_required (must be int or null)
    years = result.get("years_required")
    if years is not None and not isinstance(years, (int, float)):
        result["years_required"] = None
    elif isinstance(years, float):
        result["years_required"] = int(years)

    # Validate remote_ok (must be bool or null)
    remote = result.get("remote_ok")
    if remote is not None and not isinstance(remote, bool):
        result["remote_ok"] = None

    # Rule 2: Store in cache via storage module, never direct sqlite3
    storage.cache_extraction(db_path, desc_hash, result)

    return result
