from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from edgedash.config import Config
    from edgedash import storage
    from edgedash.planning import StopConditions


@dataclass
class AgentResult:
    """Result of an agent run."""

    agent: str
    status: str          # "ok" | "failed"
    records_touched: int
    notes: str


class Agent(ABC):
    """Base protocol for all agents."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique agent name."""
        pass

    @abstractmethod
    def run(
        self,
        config: "Config",
        storage_module: "storage",
        stop_conditions: "Optional[StopConditions]" = None,
    ) -> AgentResult:
        """Execute agent logic. Return AgentResult.

        stop_conditions: limits set by the Orchestrator (rule 29).
            Agents must not exceed them. Falls back to config defaults
            when None.
        """
        pass
