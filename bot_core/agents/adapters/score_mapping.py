"""
Score mapping adapter.

The real Almanac/Macro/Technical/LLM agents (copied in from
market-prediction-engine, unmodified) return typed, qualitative outputs built
for the CP3405 course exercise — AlmanacOutput, MacroOutput, TechnicalOutput,
SynthesisOutput (see agents/schemas.py). They were never designed to produce
a normalized -100..+100 score, because the course exercise doesn't need one —
students read the qualitative fields directly.

The trading bot's CompositeScorer does need a normalized score. Rather than
rewriting the agents to add one (which would mean maintaining two diverging
copies of code that's already tested and working in production for the
course), this module is a one-way translation layer: real output in,
AgentOutput out. If the mapping thresholds below turn out wrong, only this
file changes — the agents stay exactly as they are in market-prediction-engine.

Every threshold below is a first-pass judgment call, not backtested. Flagged
in ARCHITECTURE_DECISIONS.md ADR-005.
"""

from agents.base import AgentOutput
from agents.schemas import AlmanacOutput, MacroOutput, TechnicalOutput, SynthesisOutput


def map_almanac(output: AlmanacOutput) -> AgentOutput:
    bias_score = {"bullish": 60.0, "neutral": 0.0, "bearish": -60.0}[output.seasonal_bias]
    # Scale by win_rate distance from 50% — a 90% win-rate month says more
    # than a 56% one, even if both cross the "bullish" threshold.
    conviction = abs(output.win_rate_this_month - 0.5) * 2  # 0.0 to 1.0
    score = bias_score * (0.5 + 0.5 * conviction)
    # Confidence scales with sample size — 20 years of history is more
    # trustworthy than 5.
    confidence = min(output.years_of_history / 20.0, 1.0)
    return AgentOutput(
        agent_name="almanac",
        score=score,
        confidence=confidence,
        rationale=output.notes,
        raw_data=output.__dict__,
    )


def map_macro(output: MacroOutput) -> AgentOutput:
    regime_score = {"risk-on": 40.0, "neutral": 0.0, "risk-off": -40.0}[output.regime]
    # VIX distance from a calm baseline (~15) as a rough conviction proxy —
    # the further from calm, the more the regime call should matter.
    vix_conviction = min(abs(output.vix_level - 15.0) / 15.0, 1.0)
    score = regime_score * (0.5 + 0.5 * vix_conviction)
    # This rule-based classifier doesn't produce its own confidence figure —
    # defaulting to a flat 0.7 rather than inventing false precision.
    confidence = 0.7
    return AgentOutput(
        agent_name="macro",
        score=score,
        confidence=confidence,
        rationale=output.notes,
        raw_data=output.__dict__,
    )


def map_technical(output: TechnicalOutput) -> AgentOutput:
    trend_score = {"uptrend": 50.0, "sideways": 0.0, "downtrend": -50.0}[output.trend]
    # RSI overbought/oversold pulls the score back toward neutral — a strong
    # uptrend that's also badly overbought is a weaker "buy more" signal than
    # a clean uptrend at RSI 55.
    if output.rsi > 70:
        rsi_dampener = 1.0 - min((output.rsi - 70) / 30.0, 0.6)
    elif output.rsi < 30:
        rsi_dampener = 1.0 - min((30 - output.rsi) / 30.0, 0.6)
    else:
        rsi_dampener = 1.0
    score = trend_score * rsi_dampener
    confidence = 0.75
    return AgentOutput(
        agent_name="technical",
        score=score,
        confidence=confidence,
        rationale=output.notes,
        raw_data=output.__dict__,
    )


def map_llm_synthesis(output: SynthesisOutput) -> AgentOutput:
    direction_score = {"up": 50.0, "flat": 0.0, "down": -50.0}.get(output.weighted_direction, 0.0)
    confidence_multiplier = {"low": 0.4, "medium": 0.7, "high": 1.0}.get(output.weighted_confidence, 0.5)
    score = direction_score * confidence_multiplier
    confidence = confidence_multiplier
    return AgentOutput(
        agent_name="llm_synthesis",
        score=score,
        confidence=confidence,
        rationale=f"{len(output.model_calls)} models, weighted direction "
                   f"{output.weighted_direction} ({output.weighted_confidence} confidence)",
        raw_data=output.__dict__,
    )
