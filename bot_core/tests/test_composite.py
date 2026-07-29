"""
Tests the composite scorer in isolation — feeds it hand-built AgentOutput
objects (bypassing score_mapping entirely) so these tests are about the
WEIGHTING and VETO logic itself, not the mapping layer.

Run: pytest bot_core/tests/test_composite.py -v
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.base import AgentOutput
from agents.composite import CompositeScorer, Bias


def make_output(score, confidence, name="agent"):
    return AgentOutput(agent_name=name, score=score, confidence=confidence, rationale="test")


def test_unanimous_bullish_produces_long_bias():
    scorer = CompositeScorer()
    outputs = {
        "almanac": make_output(50, 0.9),
        "macro": make_output(40, 0.8),
        "fundamental": make_output(30, 0.7),
        "technical": make_output(60, 0.9),
        "llm_synthesis": make_output(45, 0.85),
    }
    result = scorer.score(outputs)
    assert result.bias == Bias.LONG
    assert not result.veto_triggered


def test_unanimous_bearish_produces_short_bias():
    scorer = CompositeScorer()
    outputs = {
        "almanac": make_output(-50, 0.9),
        "macro": make_output(-40, 0.8),
        "fundamental": make_output(-30, 0.7),
        "technical": make_output(-60, 0.9),
        "llm_synthesis": make_output(-45, 0.85),
    }
    result = scorer.score(outputs)
    assert result.bias == Bias.SHORT


def test_majority_low_confidence_triggers_veto():
    scorer = CompositeScorer(min_confidence_threshold=0.4)
    outputs = {
        "almanac": make_output(80, 0.1),       # low confidence
        "macro": make_output(80, 0.1),          # low confidence
        "fundamental": make_output(80, 0.1),    # low confidence
        "technical": make_output(80, 0.9),
        "llm_synthesis": make_output(80, 0.9),
    }
    result = scorer.score(outputs)
    assert result.veto_triggered, "3 of 5 agents below confidence threshold should veto regardless of score"
    assert result.bias == Bias.STAND_ASIDE


def test_missing_agents_do_not_silently_dilute_score():
    """If only 2 of 5 agents report and both are strongly bullish, the score
    should reflect THEIR conviction, not be crushed toward zero because 3
    agents are absent. Absence should trigger the veto path if severe enough,
    not silently shrink the number."""
    scorer = CompositeScorer(min_confidence_threshold=0.4)
    outputs = {
        "almanac": make_output(80, 0.9),
        "macro": make_output(80, 0.9),
        # fundamental, technical, llm_synthesis absent entirely
    }
    result = scorer.score(outputs)
    assert result.weighted_score > 50, \
        "Two strongly confident agents should produce a strong score, not a diluted one"


def test_human_override_takes_precedence():
    scorer = CompositeScorer()
    outputs = {
        "almanac": make_output(-80, 0.9),
        "macro": make_output(-80, 0.9),
        "fundamental": make_output(-80, 0.9),
        "technical": make_output(-80, 0.9),
        "llm_synthesis": make_output(-80, 0.9),
    }
    # Every agent says bearish; human overrides to bullish.
    result = scorer.score(outputs, human_override=70.0)
    assert result.bias == Bias.LONG, "Human Score must override the computed composite, per design"


def test_weights_must_sum_to_one():
    import pytest
    from agents.composite import CompositeWeights
    bad_weights = CompositeWeights(almanac=0.5, macro=0.5, fundamental=0.5,
                                     technical=0.5, llm_synthesis=0.5)
    with pytest.raises(AssertionError):
        CompositeScorer(weights=bad_weights)
