"""Source registry and ABC. Every source implements the Source protocol."""

from abc import ABC, abstractmethod
from typing import Callable, Optional

if __name__ != "__main__":
    from edgedash.config import Config


class SourceError(Exception):
    """Raised when a source fails. Caught by Fetcher, does not crash the cycle."""

    pass


class Source(ABC):
    """Base class for all job sources."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique source identifier."""
        pass

    @abstractmethod
    def fetch(self, config: "Config") -> list[dict]:
        """Fetch and return normalised listings.

        Returns:
            list[dict] with keys: source, external_id, title, company, location,
                url, description, posted_at, raw.
                Missing values are None (never empty string, never "N/A").

        Raises:
            SourceError if the source fails (caught by Fetcher).
        """
        pass


# Registry: maps source name to Source class
SOURCES: dict[str, type[Source]] = {}


def register(cls: type[Source]) -> type[Source]:
    """Decorator to register a Source. Usage: @register."""
    SOURCES[cls().name] = cls
    return cls


def get_source(name: str) -> Optional[Source]:
    """Get a source by name, or None if not found."""
    if name not in SOURCES:
        return None
    return SOURCES[name]()
