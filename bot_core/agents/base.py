"""
Base Agent interface. Mirrors the pattern already established in the
market-prediction-engine repo (backend/agents/base.py) so this can be merged
back rather than diverging into a second standard.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class AgentOutput:
    agent_name: str
    score: float  # normalized -100 (max bearish) to +100 (max bullish)
    confidence: float  # 0.0 to 1.0 — how much weight this run deserves
    rationale: str  # short human-readable explanation, shown in the Human Score UI
    raw_data: dict = field(default_factory=dict)
    generated_at: datetime = field(default_factory=datetime.utcnow)
    stale: bool = False  # True if this ran on cached/fallback data


class Agent(ABC):
    name: str = "base_agent"

    @abstractmethod
    def run(self, context: dict) -> AgentOutput:
        """context carries whatever shared state the pipeline orchestrator
        passes in (target date, symbols in scope, prior agent outputs if
        sequencing matters)."""
        raise NotImplementedError
