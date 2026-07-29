"""
Regression test for a real bug caught by the 2026-07-29 live dry-run:
FundamentalAgent.run() crashed with TypeError when earnings_score was None
(outside earnings season) because combined_score averaged it directly
without a None check. This test exists specifically so that bug can't
silently come back.

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
    with patch.object(agent, "_pull_macro_series", return_value={"yield_curve_10y2y": []}):
        result = agent.run({"earnings_season_active": False})
    assert isinstance(result.score, float)
    assert result.confidence == 0.6, "Outside earnings season, confidence should reflect the missing sub-signal"


def test_run_uses_averaged_score_during_earnings_season():
    """When earnings tone IS available (once wired to the LLM synthesis
    layer), it should actually be averaged in, not ignored."""
    agent = FundamentalAgent(fred_api_key="dummy-key-for-test")
    with patch.object(agent, "_pull_macro_series", return_value={"yield_curve_10y2y": []}), \
         patch.object(agent, "_earnings_tone", return_value=(40.0, "mocked earnings tone")):
        result = agent.run({"earnings_season_active": True})
    # macro score with no yield curve data defaults to 0.0 (see _score_macro);
    # averaged with a mocked earnings score of 40.0 -> 20.0
    assert result.score == 20.0
    assert result.confidence == 1.0


def test_run_skips_cleanly_without_api_key():
    agent = FundamentalAgent(fred_api_key=None)
    result = agent.run({})
    assert result.confidence == 0.0
    assert result.stale is True
