"""
strategy.py — Bull Put Spread Selection Logic
Selects optimal strikes based on delta target and market intel score.
"""

import datetime
import json
from config import (
    UNDERLYING, NUM_SPREADS, SPREAD_WIDTH,
    TARGET_DELTA, MIN_PREMIUM, MAX_PREMIUM,
    DAYS_TO_EXPIRY, MIN_BULLISH_SCORE, BEARISH_VETO_SCORE, VIX_MAX
)
from market_intel import get_vix


def get_next_friday():
    """Return next Friday date for weekly expiry."""
    today = datetime.date.today()
    days_ahead = 4 - today.weekday()  # Friday = 4
    if days_ahead <= 0:
        days_ahead += 7
    return today + datetime.timedelta(days=days_ahead)


def should_trade(intel):
    """
    Gate check — only trade if conditions are met.
    Returns (bool, reason_string)
    """
    composite = intel["composite"]
    vix = get_vix()

    if intel["bias"] == "BEARISH" and composite <= BEARISH_VETO_SCORE:
        return False, f"VETO: Bearish composite score {composite} below threshold {BEARISH_VETO_SCORE}"

    if composite < MIN_BULLISH_SCORE:
        return False, f"SKIP: Composite score {composite} below minimum {MIN_BULLISH_SCORE}"

    if vix and vix > VIX_MAX:
        return False, f"SKIP: VIX {vix:.1f} above maximum {VIX_MAX}"

    return True, f"PROCEED: Composite {composite} | Bias {intel['bias']} | VIX {vix}"


def calculate_strikes(spx_price, intel_score):
    """
    Calculate short and long put strikes.
    Adjusts distance from ATM based on intel score confidence.
    """
    # Higher intel score = more confident = can sell closer to ATM
    # Lower intel score = more cautious = sell further OTM
    if intel_score >= 65:
        otm_buffer = 0.04   # 4% OTM
    elif intel_score >= 55:
        otm_buffer = 0.05   # 5% OTM (standard)
    else:
        otm_buffer = 0.06   # 6% OTM (cautious)

    short_put = round(spx_price * (1 - otm_buffer) / 5) * 5  # Round to nearest 5
    long_put  = short_put - SPREAD_WIDTH

    return short_put, long_put


def build_recommendation(spx_price, intel, estimated_premium=None):
    """
    Build the full trade recommendation object.
    `intel` must be the real composite intel dict (from orchestrator.py's
    AI Board output, already written to state) — this function no longer
    computes its own fake placeholder intel via market_intel.get_composite_score().
    In production: estimated_premium comes from live options chain.
    """
    proceed, reason = should_trade(intel)
    expiry = get_next_friday()
    short_put, long_put = calculate_strikes(spx_price, intel["composite"])

    # Estimate premium if not provided (placeholder)
    if estimated_premium is None:
        # Rough estimate: 1.5-2.5% of spread width based on VIX
        estimated_premium = round(SPREAD_WIDTH * 1.8, 0)

    max_risk   = (SPREAD_WIDTH - estimated_premium / 100) * 100
    max_profit = estimated_premium * NUM_SPREADS
    total_risk = max_risk * NUM_SPREADS

    recommendation = {
        "date":           datetime.date.today().isoformat(),
        "expiry":         expiry.isoformat(),
        "underlying":     UNDERLYING,
        "strategy":       "Bull Put Spread",
        "spx_price":      spx_price,
        "short_put":      short_put,
        "long_put":       long_put,
        "spread_width":   SPREAD_WIDTH,
        "num_spreads":    NUM_SPREADS,
        "est_premium":    estimated_premium,
        "max_profit":     max_profit,
        "max_risk":       total_risk,
        "profit_target":  round(max_profit * 0.50, 0),
        "proceed":        proceed,
        "reason":         reason,
        "intel":          intel,
        "status":         "PENDING_APPROVAL",
        "approved":       False,
        "executed":       False,
        "human_score":    55
    }

    return recommendation


if __name__ == "__main__":
    # Test with approximate current SPX price + sample intel
    sample_intel = {"composite": 60, "bias": "BULLISH"}
    rec = build_recommendation(spx_price=7469, intel=sample_intel)
    print(json.dumps(rec, indent=2))
