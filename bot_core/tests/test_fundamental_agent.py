"""
Regression test for a real bug caught by the 2026-07-29 live dry-run:
FundamentalAgent.run() crashed with TypeError when earnings_score was None
(outside earnings season) because combined_score averaged it directly
without a None check. This test exists specifically so that bug can't
silently come back.

Also covers the 2026-07-31 addition of the 10Y-yield-direction signal
(_score_10y_direction), added because the original agent only scored
yield-CURVE-SHAPE (T10Y2Y regime), and had no signal at all for a 10Y
yield-LEVEL move like the one that happened that week — the discount-rate
channel that hits long-duration growth/tech (NDX) names hardest.

Run: pytest bot_core/tests/test_fundamental_agent.py -v
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unittest.mock import patch
from agents.fundamental.agent import FundamentalAgent


def test_run_does_not_crash_outside_earnings_season():
    """The exact failure mode from the live run: earnings_season_active=False
    must not raise TypeError when combining scores."""
    agent = FundamentalAgent(fred_api_key="dummy-key-for-test")
    with patch.object(agent, "_pull_macro_series", return_value={"yield_curve_10y2y": [], "treasury_10y": []}):
        result = agent.run({"earnings_season_active": False})
    assert isinstance(result.score, float)
    assert result.confidence == 0.6, "Outside earnings season, confidence should reflect the missing sub-signal"


def test_run_uses_averaged_score_during_earnings_season():
    """When earnings tone IS available (once wired to the LLM synthesis
    layer), it should actually be averaged in, not ignored."""
    agent = FundamentalAgent(fred_api_key="dummy-key-for-test")
    with patch.object(agent, "_pull_macro_series", return_value={"yield_curve_10y2y": [], "treasury_10y": []}), \
         patch.object(agent, "_earnings_tone", return_value=(40.0, "mocked earnings tone")):
        result = agent.run({"earnings_season_active": True})
    # macro score with no yield curve or 10Y data defaults to 0.0 (both
    # sub-components neutral); averaged with a mocked earnings score of
    # 40.0 -> 20.0
    assert result.score == 20.0
    assert result.confidence == 1.0


def test_run_skips_cleanly_without_api_key():
    agent = FundamentalAgent(fred_api_key=None)
    result = agent.run({})
    assert result.confidence == 0.0
    assert result.stale is True


def test_10y_rising_yield_is_bearish_for_growth():
    """Rising 10Y (prior 4.20 -> latest 4.35, +15bps) should score negative
    — the discount-rate headwind for long-duration growth/tech names."""
    agent = FundamentalAgent(fred_api_key="dummy-key-for-test")
    macro_data = {
        "yield_curve_10y2y": [],
        "treasury_10y": [{"value": "4.35"}, {"value": "4.20"}],
    }
    score, rationale = agent._score_10y_direction(macro_data)
    assert score < 0
    assert "rising" in rationale
    assert "headwind" in rationale


def test_10y_falling_yield_is_bullish_for_growth():
    """Falling 10Y (prior 4.35 -> latest 4.20, -15bps) should score positive
    — the discount-rate tailwind for long-duration growth/tech names."""
    agent = FundamentalAgent(fred_api_key="dummy-key-for-test")
    macro_data = {
        "yield_curve_10y2y": [],
        "treasury_10y": [{"value": "4.20"}, {"value": "4.35"}],
    }
    score, rationale = agent._score_10y_direction(macro_data)
    assert score > 0
    assert "falling" in rationale
    assert "tailwind" in rationale


def test_10y_score_is_capped_at_25_points():
    """A large move (e.g. +100bps) should be capped, not allowed to
    dominate the composite on its own."""
    agent = FundamentalAgent(fred_api_key="dummy-key-for-test")
    macro_data = {
        "yield_curve_10y2y": [],
        "treasury_10y": [{"value": "5.35"}, {"value": "4.35"}],
    }
    score, _ = agent._score_10y_direction(macro_data)
    assert score == -25.0


def test_10y_missing_data_defaults_neutral():
    agent = FundamentalAgent(fred_api_key="dummy-key-for-test")
    score, rationale = agent._score_10y_direction({"treasury_10y": []})
    assert score == 0.0
    assert "unavailable" in rationale
