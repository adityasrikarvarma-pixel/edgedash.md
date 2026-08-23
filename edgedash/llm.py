"""Single door to language models per steering rule 15. No other file imports LLM SDKs."""

import hashlib
import json
import os
import re
import sys
import time
from collections import deque
from typing import Any, Optional

from edgedash.config import Config


class LLMError(Exception):
    """LLM operation failed. Caller must handle per steering rule 17."""

    pass


# Rate limiting: track call times in a rolling window
_call_times: deque = deque(maxlen=15)  # Max 15 calls per minute
_last_call_time: float = 0.0


def _rate_limit() -> None:
    """Enforce rate limit: 1 call/sec, max 15/min. Sleep if needed."""
    global _last_call_time

    # Enforce 1 second minimum between calls
    now = time.time()
    if _last_call_time > 0:
        elapsed = now - _last_call_time
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
    _last_call_time = time.time()

    # Enforce rolling 15 per minute (remove calls older than 60s)
    now = time.time()
    while _call_times and _call_times[0] < now - 60:
        _call_times.popleft()

    # If we have 15 calls in the last minute, sleep
    if len(_call_times) >= 15:
        wait_until = _call_times[0] + 60
        sleep_time = wait_until - time.time()
        if sleep_time > 0:
            time.sleep(sleep_time)

    _call_times.append(time.time())


def _strip_fences(text: str) -> str:
    """Strip markdown code fences and prose before/after JSON."""
    # Remove markdown fences
    text = re.sub(r"```(?:json)?\s*", "", text)
    # Find first { and last } to extract JSON
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text.strip()


def _validate_schema(data: Any, schema: dict) -> None:
    """Validate data against schema. Raise ValueError if mismatch.
    
    A null/None value is accepted for any field — the caller normalises
    nulls to safe defaults after validation. This prevents models that
    return null instead of [] or null instead of false from failing.
    """
    if not isinstance(data, dict):
        raise ValueError("Response must be a JSON object")

    for key, type_name in schema.items():
        if key not in data:
            raise ValueError(f"Missing required key: {key}")

        value = data[key]

        # null is always acceptable — caller handles normalisation
        if value is None:
            continue

        if type_name == "string" and not isinstance(value, str):
            raise ValueError(f"Key '{key}' must be a string, got {type(value).__name__}")
        elif type_name == "number" and not isinstance(value, (int, float)):
            raise ValueError(f"Key '{key}' must be a number, got {type(value).__name__}")
        elif type_name == "array" and not isinstance(value, list):
            raise ValueError(f"Key '{key}' must be an array, got {type(value).__name__}")


def _call_gemini(prompt: str, schema: dict) -> dict:
    """Call Google Gemini API. Requires GEMINI_API_KEY env var."""
    try:
        import google.generativeai as genai
    except ImportError:
        raise LLMError(
            "google-generativeai not installed. pip install google-generativeai"
        )

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise LLMError(
            "GEMINI_API_KEY not set. Add it to .env or set the environment variable."
        )

    genai.configure(api_key=api_key)
    config = Config.load()
    model = genai.GenerativeModel(config.llm_model)

    # Build prompt with schema instruction
    schema_str = json.dumps(schema, indent=2)
    full_prompt = f"""{prompt}

Respond with ONLY a JSON object matching this schema (no markdown, no prose):
{schema_str}"""

    try:
        response = model.generate_content(full_prompt)
        return response.text
    except Exception as e:
        # Handle quota/429 errors specially
        error_str = str(e).lower()
        if "429" in error_str or "quota" in error_str or "rate" in error_str:
            raise LLMError(f"Rate limit or quota exceeded: {e}") from e
        raise LLMError(f"Gemini API error: {e}") from e


def _call_ollama(prompt: str, schema: dict) -> dict:
    """Call local Ollama HTTP API. No API key needed."""
    try:
        import requests
    except ImportError:
        raise LLMError("requests not installed. pip install requests")

    config = Config.load()
    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")

    schema_str = json.dumps(schema, indent=2)
    full_prompt = f"""{prompt}

Respond with ONLY a JSON object matching this schema (no markdown, no prose):
{schema_str}"""

    try:
        response = requests.post(
            ollama_url,
            json={"model": config.llm_model, "prompt": full_prompt, "stream": False},
            timeout=120,  # llama3.2 on CPU can be slow for long descriptions
        )
        response.raise_for_status()
        result = response.json()
        return result.get("response", "")
    except requests.exceptions.RequestException as e:
        raise LLMError(f"Ollama API error: {e}") from e


def complete_json(
    prompt: str, schema: dict, *, max_retries: int = 1
) -> dict:
    """Fetch JSON from LLM, validate against schema, retry on failure.

    Args:
        prompt: Instruction to send to the model
        schema: Dict mapping key names to type names ("string", "number", "array")
        max_retries: Retry count on validation failure (default 1)

    Returns:
        Parsed and validated JSON dict

    Raises:
        LLMError: If parsing/validation fails after retries or provider is not found
    """
    config = Config.load()

    # Select provider
    if config.llm_provider == "gemini":
        call_fn = _call_gemini
    elif config.llm_provider == "ollama":
        call_fn = _call_ollama
    else:
        raise LLMError(
            f"Unknown LLM provider: {config.llm_provider}. "
            "Set llm_provider to 'gemini' or 'ollama' in config.yaml"
        )

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            _rate_limit()

            # Call LLM with exponential backoff for rate limits
            for retry in range(3):
                try:
                    raw_response = call_fn(prompt, schema)
                    break
                except LLMError as e:
                    if "rate limit" in str(e).lower() or "quota" in str(e).lower():
                        if retry < 2:
                            wait = 2 ** retry
                            time.sleep(wait)
                            continue
                    raise

            # Strip markdown fences
            clean_json = _strip_fences(raw_response)

            # Parse JSON
            try:
                data = json.loads(clean_json)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON: {e}")

            # Validate schema
            _validate_schema(data, schema)

            return data

        except (ValueError, json.JSONDecodeError) as e:
            last_error = str(e)
            if attempt < max_retries:
                # Retry with error message
                error_instruction = f"\n\nERROR on previous attempt: {last_error}\nYou MUST respond with ONLY valid JSON, no markdown fence, no prose."
                prompt = prompt + error_instruction
            else:
                raise LLMError(f"Failed to get valid JSON after {max_retries + 1} attempts. Last error: {last_error}")


if __name__ == "__main__":
    # CLI check: python -m edgedash.llm --check
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        try:
            cfg = Config.load()
            print(f"\n✓ LLM Module Check")
            print(f"  Provider:    {cfg.llm_provider}")
            print(f"  Model:       {cfg.llm_model}")
            print(f"  Batch size:  {cfg.llm_batch_size}")

            # Test call
            test_prompt = "Respond with a single word: 'ok'"
            test_schema = {"status": "string"}
            result = complete_json(test_prompt, test_schema)

            print(f"  Test call:   ✓ {result}")
            print(f"\n✓ LLM is working\n")

        except Exception as e:
            print(f"\n❌ LLM check failed: {e}\n", file=sys.stderr)
            sys.exit(1)
