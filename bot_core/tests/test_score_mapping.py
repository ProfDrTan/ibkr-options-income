"""
Pure unit tests — no network, no gateway, no API keys. These test the
translation logic in score_mapping.py against known input/output pairs.
They do NOT test whether the real agents (almanac_agent.run(), etc.) produce
correct real-world signals — that requires live network access and is a
separate integration test (see tests/test_integration_live.py).

Run: pytest bot_core/tests/test_score_mapping.py -v
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import date
from agents.schemas import AlmanacOutput, MacroOutput, TechnicalOutput, SynthesisOutput, ModelCall
from agents.adapters.score_mapping import map_almanac, map_macro, map_technical, map_llm_synthesis


def test_almanac_strong_bullish_high_confidence():
    out = AlmanacOutput(month=7, seasonal_bias="bullish", avg_return_this_month=0.02,
                         win_rate_this_month=0.85, years_of_history=25, notes="strong")
    mapped = map_almanac(out)
    assert mapped.score > 50, "Strong bullish + high win-rate should score well above the midpoint"
    assert mapped.confidence > 0.9, "25 years of history should produce near-max confidence"


def test_almanac_weak_signal_low_confidence():
    out = AlmanacOutput(month=7, seasonal_bias="bullish", avg_return_this_month=0.001,
                         win_rate_this_month=0.51, years_of_history=3, notes="thin")
    mapped = map_almanac(out)
    assert mapped.confidence < 0.2, "3 years of history should produce low confidence"
    # A 51% win rate is barely different from a coin flip — score should be
    # damped toward zero even though the bias label itself says "bullish".
    assert abs(mapped.score) < 40


def test_macro_risk_off_scores_negative():
    out = MacroOutput(regime="risk-off", dxy_change_1w=1.2, yield_10y_change_1w=0.3,
                       oil_change_1w=2.1, gold_change_1w=1.5, vix_level=32.0, notes="stress")
    mapped = map_macro(out)
    assert mapped.score < 0, "risk-off regime must map to a negative score"


def test_macro_neutral_regime_near_zero():
    out = MacroOutput(regime="neutral", dxy_change_1w=0.1, yield_10y_change_1w=0.0,
                       oil_change_1w=0.2, gold_change_1w=0.0, vix_level=15.0, notes="calm")
    mapped = map_macro(out)
    assert mapped.score == 0.0


def test_technical_overbought_dampens_score():
    strong_but_overbought = TechnicalOutput(ticker="^GSPC", trend="uptrend", rsi=85.0,
                                             pct_from_50dma=5.0, pct_from_200dma=8.0,
                                             momentum_20d=3.0, notes="overbought")
    clean_uptrend = TechnicalOutput(ticker="^GSPC", trend="uptrend", rsi=55.0,
                                     pct_from_50dma=2.0, pct_from_200dma=4.0,
                                     momentum_20d=1.5, notes="healthy")
    overbought_score = map_technical(strong_but_overbought).score
    clean_score = map_technical(clean_uptrend).score
    assert overbought_score < clean_score, \
        "An overbought uptrend should score lower than a clean one — same direction, more risk"


def test_llm_synthesis_low_confidence_shrinks_score():
    high_conf = SynthesisOutput(ticker="^GSPC", prediction_date=date.today(),
        model_calls=[ModelCall("A", "up", 0.5, 1.0, "high")],
        weighted_direction="up", weighted_range_low=0.5, weighted_range_high=1.0,
        weighted_confidence="high")
    low_conf = SynthesisOutput(ticker="^GSPC", prediction_date=date.today(),
        model_calls=[ModelCall("A", "up", 0.5, 1.0, "low")],
        weighted_direction="up", weighted_range_low=0.5, weighted_range_high=1.0,
        weighted_confidence="low")
    assert map_llm_synthesis(high_conf).score > map_llm_synthesis(low_conf).score
