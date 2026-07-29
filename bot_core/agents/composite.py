"""
Composite scoring engine. Generalizes the weighted-composite-score pattern
already proven in the SPX bull-put-spread bot (Almanac + Macro + Technical +
LLM synthesis + Human Score -> proceed/skip/veto) so the same engine can gate
futures direction too.

See docs/ARCHITECTURE_DECISIONS.md ADR-004 for the weekly-bias /
faster-technical-trigger cadence split this implements.
"""

from dataclasses import dataclass
from enum import Enum
from agents.base import AgentOutput


class Bias(str, Enum):
    LONG = "long"
    SHORT = "short"
    STAND_ASIDE = "stand_aside"


@dataclass
class CompositeWeights:
    """Starting weights — NOT backtested/calibrated yet. Treat as a first
    pass to be tuned against historical outcomes before trusting with size."""
    almanac: float = 0.20
    macro: float = 0.20
    fundamental: float = 0.20
    technical: float = 0.25
    llm_synthesis: float = 0.15

    def total(self) -> float:
        return (self.almanac + self.macro + self.fundamental
                + self.technical + self.llm_synthesis)


@dataclass
class CompositeResult:
    weighted_score: float  # -100 to +100
    bias: Bias
    contributing_agents: dict  # agent_name -> AgentOutput, for the audit trail
    veto_triggered: bool
    veto_reason: str = ""


class CompositeScorer:
    def __init__(self, weights: CompositeWeights = None,
                 min_confidence_threshold: float = 0.4,
                 long_threshold: float = 15.0,
                 short_threshold: float = -15.0):
        self.weights = weights or CompositeWeights()
        assert abs(self.weights.total() - 1.0) < 0.01, \
            "Composite weights must sum to 1.0 — check CompositeWeights"
        self.min_confidence_threshold = min_confidence_threshold
        self.long_threshold = long_threshold
        self.short_threshold = short_threshold

    def score(self, agent_outputs: dict[str, AgentOutput],
              human_override: float = None) -> CompositeResult:
        """agent_outputs keys expected: 'almanac', 'macro', 'fundamental',
        'technical', 'llm_synthesis'. Missing agents are excluded and their
        weight is NOT silently redistributed — that would quietly change the
        formula's meaning. Instead, low aggregate confidence triggers a veto.
        """
        weighted_sum = 0.0
        weight_used = 0.0
        low_confidence_agents = []

        agent_weight_map = {
            "almanac": self.weights.almanac,
            "macro": self.weights.macro,
            "fundamental": self.weights.fundamental,
            "technical": self.weights.technical,
            "llm_synthesis": self.weights.llm_synthesis,
        }

        for agent_key, weight in agent_weight_map.items():
            output = agent_outputs.get(agent_key)
            if output is None:
                continue
            if output.confidence < self.min_confidence_threshold:
                low_confidence_agents.append(agent_key)
                continue
            weighted_sum += output.score * weight * output.confidence
            weight_used += weight

        if weight_used == 0:
            return CompositeResult(
                weighted_score=0.0,
                bias=Bias.STAND_ASIDE,
                contributing_agents=agent_outputs,
                veto_triggered=True,
                veto_reason="No agent met the minimum confidence threshold — "
                            "standing aside rather than trading on noise.",
            )

        # Normalize by the weight actually used, so a missing/low-confidence
        # agent doesn't silently shrink the effective score toward zero.
        normalized_score = weighted_sum / weight_used

        if human_override is not None:
            # Human Score is a hard override, not another weighted input —
            # matches the existing bot's design where the human has final say.
            normalized_score = human_override

        if normalized_score >= self.long_threshold:
            bias = Bias.LONG
        elif normalized_score <= self.short_threshold:
            bias = Bias.SHORT
        else:
            bias = Bias.STAND_ASIDE

        veto = len(low_confidence_agents) >= 3  # majority of 5 agents unreliable
        veto_reason = (
            f"{len(low_confidence_agents)} of 5 agents below confidence "
            f"threshold: {', '.join(low_confidence_agents)}"
            if veto else ""
        )
        if veto:
            bias = Bias.STAND_ASIDE

        return CompositeResult(
            weighted_score=normalized_score,
            bias=bias,
            contributing_agents=agent_outputs,
            veto_triggered=veto,
            veto_reason=veto_reason,
        )
