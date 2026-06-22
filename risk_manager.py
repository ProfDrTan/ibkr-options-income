"""
risk_manager.py — Position Sizing and Risk Rules
Enforces hard limits on exposure at all times.
"""

import json
import datetime
from config import (
    MAX_RISK_PER_SPREAD, MAX_TOTAL_RISK,
    PROFIT_TARGET_PCT, MAX_LOSS_PCT,
    THURSDAY_CLOSE, NUM_SPREADS, SPREAD_WIDTH
)


def check_portfolio_risk(open_positions):
    """
    Check total portfolio risk against limits.
    Returns (safe: bool, message: str)
    """
    total_risk = sum(p.get("max_risk", 0) for p in open_positions)

    if total_risk > MAX_TOTAL_RISK:
        return False, f"RISK LIMIT: Total risk ${total_risk:,.0f} exceeds max ${MAX_TOTAL_RISK:,.0f}"

    return True, f"RISK OK: Total exposure ${total_risk:,.0f} of ${MAX_TOTAL_RISK:,.0f} limit"


def should_close_position(position):
    """
    Evaluate whether a position should be closed.
    Returns (close: bool, reason: str)
    """
    premium_received = position.get("premium_received", 0)
    current_value    = position.get("current_value", premium_received)
    profit_pct       = (premium_received - current_value) / premium_received if premium_received else 0

    # 50% profit target hit
    if profit_pct >= PROFIT_TARGET_PCT:
        return True, f"PROFIT TARGET: {profit_pct:.0%} gain achieved"

    # Stop loss triggered
    loss_pct = (current_value - premium_received) / premium_received if premium_received else 0
    if loss_pct >= MAX_LOSS_PCT:
        return True, f"STOP LOSS: {loss_pct:.0%} loss — roll or close"

    # Thursday forced close
    if THURSDAY_CLOSE and datetime.date.today().weekday() == 3:
        return True, "THURSDAY CLOSE: Removing overnight gamma risk"

    return False, "HOLD: No close criteria met"


def calculate_position_size(available_capital, intel_score):
    """
    Adjust number of spreads based on available capital and conviction.
    Never risk more than 3% of account per week.
    """
    max_by_capital = int(available_capital * 0.03 / MAX_RISK_PER_SPREAD)
    max_by_config  = NUM_SPREADS

    # Reduce size if intel score is marginal
    if intel_score < 58:
        conviction_multiplier = 0.5
    elif intel_score < 62:
        conviction_multiplier = 0.75
    else:
        conviction_multiplier = 1.0

    final_size = min(max_by_capital, max_by_config)
    final_size = max(1, int(final_size * conviction_multiplier))

    return final_size


if __name__ == "__main__":
    # Test risk check
    test_positions = [{"max_risk": 800} for _ in range(10)]
    ok, msg = check_portfolio_risk(test_positions)
    print(msg)
