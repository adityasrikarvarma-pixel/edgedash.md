"""HTTP helper for all network requests. The ONLY place in the codebase that performs HTTP requests."""

import time
from typing import Any, Optional

try:
    import requests
except ImportError:
    requests = None

from edgedash.sources.base import SourceError


class NetworkError(SourceError):
    """HTTP request failed."""

    pass


def get_json(
    url: str,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: float = 10.0,
    max_retries: int = 2,
) -> dict[str, Any]:
    """Fetch JSON from URL with timeout, retries, and User-Agent.

    Args:
        url: Target URL
        params: Query parameters (dict)
        headers: Additional headers (dict)
        timeout: Request timeout in seconds (default 10)
        max_retries: Maximum retry attempts (default 2)

    Returns:
        Parsed JSON response

    Raises:
        NetworkError: If all retries fail
    """
    if requests is None:
        raise NetworkError("requests library not installed. pip install requests")

    if headers is None:
        headers = {}

    # Set real User-Agent (steering rule 11)
    if "User-Agent" not in headers:
        headers["User-Agent"] = (
            "EdgeDash/1.0 (+https://github.com/user/edgedash.md)"
        )

    last_error = None
    for attempt in range(max_retries):
        try:
            response = requests.get(
                url, params=params, headers=headers, timeout=timeout
            )
            response.raise_for_status()
            return response.json()

        except requests.exceptions.Timeout:
            last_error = f"Timeout after {timeout}s"
        except requests.exceptions.ConnectionError as e:
            last_error = f"Connection error: {e}"
        except requests.exceptions.HTTPError as e:
            last_error = f"HTTP {e.response.status_code}: {e}"
        except requests.exceptions.RequestException as e:
            last_error = f"Request failed: {e}"
        except ValueError as e:
            last_error = f"Invalid JSON: {e}"

        if attempt < max_retries - 1:
            # Exponential backoff: 1s, 2s, etc.
            wait = 2 ** attempt
            time.sleep(wait)

    raise NetworkError(f"Failed after {max_retries} retries. Last error: {last_error}")
