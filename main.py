"""
main.py — Master Controller
Orchestrates the full weekly options income workflow.
"""

import json
import datetime
import sys
import os


def load_state():
    """Load current trade state from JSON file."""
    try:
        with open("data/trade_state.json", "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    """Save trade state to JSON file."""
    os.makedirs("data", exist_ok=True)
    with open("data/trade_state.json", "w") as f:
        json.dump(state, f, indent=2)


def run_monday_open():
    """
    Monday 09:35 ET — Generate recommendation and update website.
    """
    print(f"[{datetime.datetime.utcnow().isoformat()}] MONDAY OPEN — Generating recommendation")

    from market_intel import get_composite_score
    from strategy import build_recommendation
    from alerts import alert_recommendation_ready
    from market_data import get_spx_price

    # Get market intelligence
    intel = get_composite_score()
    print(f"Intel composite: {intel['composite']} | Bias: {intel['bias']}")

    # Live SPX price (Yahoo -> Stooq -> last-known-stale, in that order)
    spx_price, price_source = get_spx_price()
    print(f"SPX price: {spx_price} (source: {price_source})")
    rec = build_recommendation(spx_price=spx_price)
    rec["spx_price_source"] = price_source

    # Save state
    state = load_state()
    state["recommendation"] = rec
    state["last_updated"]   = datetime.datetime.utcnow().isoformat()
    save_state(state)

    print(f"Recommendation: {rec['strategy']} | Proceed: {rec['proceed']}")

    # Send Telegram alert
    if rec["proceed"]:
        alert_recommendation_ready(rec)
        print("Telegram alert sent — awaiting Professor Dr. Tan approval")
    else:
        print(f"Trade skipped: {rec['reason']}")

    return rec


def run_daily_check():
    """
    Daily 15:45 ET — Check profit targets on open positions.
    """
    print(f"[{datetime.datetime.utcnow().isoformat()}] DAILY CHECK")

    state = load_state()
    positions = state.get("open_positions", [])

    if not positions:
        print("No open positions to check")
        return

    from risk_manager import should_close_position
    from alerts import alert_profit_target, alert_stop_loss, alert_thursday_close

    is_thursday = datetime.date.today().weekday() == 3
    closed = []

    for pos in positions:
        close, reason = should_close_position(pos)
        if close:
            pnl = pos.get("premium_received", 0) - pos.get("current_value", 0)
            closed.append((pos, pnl, reason))
            print(f"CLOSE: {reason} | P&L: {pnl:+.0f}")

    if is_thursday and not closed:
        # Force close all
        for pos in positions:
            pnl = pos.get("premium_received", 0) * 0.3  # Estimate remaining value
            closed.append((pos, pnl, "Thursday forced close"))

    if closed:
        total_pnl = sum(c[1] for c in closed)
        if is_thursday:
            alert_thursday_close(len(closed), total_pnl)

    state["open_positions"] = [p for p in positions
                                if p not in [c[0] for c in closed]]
    save_state(state)


def run_execute():
    """
    Execute approved trade — called when Professor Dr. Tan approves.
    """
    print(f"[{datetime.datetime.utcnow().isoformat()}] EXECUTE — Processing approval")

    state = load_state()
    rec = state.get("recommendation", {})

    if not rec.get("approved"):
        print("Trade not approved — nothing to execute")
        return

    if rec.get("executed"):
        print("Trade already executed")
        return

    from executor import place_bull_put_spread
    from alerts import alert_trade_executed

    result = place_bull_put_spread(rec)

    if result["success"]:
        rec["executed"] = True
        rec["status"]   = "EXECUTED"
        rec["execution_details"] = result

        # Add to open positions
        open_positions = state.get("open_positions", [])
        open_positions.append({
            "id":               f"SPX_BPS_{rec['date']}",
            "short_put":        rec["short_put"],
            "long_put":         rec["long_put"],
            "expiry":           rec["expiry"],
            "quantity":         rec["num_spreads"],
            "premium_received": rec["est_premium"] * rec["num_spreads"],
            "current_value":    rec["est_premium"] * rec["num_spreads"],
            "max_risk":         rec["max_risk"],
            "opened":           datetime.datetime.utcnow().isoformat()
        })

        state["open_positions"] = open_positions
        state["recommendation"] = rec
        save_state(state)

        alert_trade_executed(result)
        print("Trade executed successfully")
    else:
        print(f"Execution failed: {result['error']}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"

    if mode == "monday":
        run_monday_open()
    elif mode == "check":
        run_daily_check()
    elif mode == "execute":
        run_execute()
    else:
        print(f"Unknown mode: {mode}. Use: monday | check | execute")
