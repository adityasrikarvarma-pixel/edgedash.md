"""Job sources layer. Every source is registered via @register decorator."""

from edgedash.sources.base import Source, SourceError, get_source, register
from edgedash.sources.arbeitnow import ArbeitnowSource

__all__ = ["Source", "SourceError", "get_source", "register", "ArbeitnowSource"]
