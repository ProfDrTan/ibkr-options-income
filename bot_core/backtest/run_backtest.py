"""
Point-in-time backtest for the composite scoring engine.

WHY THIS ISN'T JUST "CALL THE REAL AGENTS ON OLD DATES":
almanac_agent.run() and technical_agent.run() and macro_agent.run() all pull
data relative to "right now" via yfinance — none of them accept a historical
as_of date that actually restricts the data window (almanac_agent.py *has*
an as_of parameter, but its yf.Ticker().history(period="20y") call always
ends at today regardless of as_of — a real lookahead bug, not a design
choice). Backtesting by calling run() on old dates would silently leak
future data into "past" predictions and produce a fake-looking backtest.

WHAT THIS FILE ACTUALLY DOES INSTEAD:
Downloads full historical price series ONCE, then re-implements the
point-in-time slicing itself, while reusing the actual PURE, testable
functions already inside the real agent modules — compute_monthly_stats(),
classify_regime(), compute_rsi() — rather than duplicating that logic.
Those functions take data as arguments; they don't reach out to "now"
themselves, so they're safe to call with historical slices. This is
importing and reusing tested logic, not forking it — see ADR-005/ADR-007.

Every score-mapping threshold used here is IDENTICAL to score_mapping.py —
imported directly, not re-typed, so the backtest tests the actual production
mapping, not a lookalike.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# run_backtest.py lives at <workspace>/ibkr-options-income/bot_core/backtest/
# parents[0]=backtest, [1]=bot_core, [2]=ibkr-options-income, [3]=<workspace>
# market-prediction-engine is checked out as a SIBLING of ibkr-options-income,
# i.e. under parents[3], not parents[2] — this was wrong before and caused a
# silent ImportError at module load time, before any try/except could catch it.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "market-prediction-engine"))

import json
from datetime import datetime, timezone, date
import yfinance as yf

from agents.almanac.almanac_agent import compute_monthly_stats
from agents.macro.macro_agent import classify_regime, TICKERS as MACRO_TICKERS
from agents.technical.technical_agent import compute_rsi
from agents.schemas import AlmanacOutput, MacroOutput, TechnicalOutput
from agents.adapters.score_mapping import map_almanac, map_macro, map_technical
from agents.composite import CompositeScorer, Bias


def download_history(ticker: str, period: str = "max", interval: str = "1d"):
    hist = yf.Ticker(ticker).history(period=period, interval=interval)
    hist = hist.reset_index()
    return hist


OTM_LEVELS = [0.01, 0.02, 0.03, 0.05]  # 1%, 2%, 3%, 5% below spot — spans the
                                        # range actually used live (the 2026-07-29
                                        # SPX trade needed ~1.1% OTM to hit premium
                                        # target; the documented strategy rule is ~5%)


def compute_containment(daily_df, idx, forward_days, otm_levels):
    """For a short put struck otm_pct below spot at idx, did price ever trade
    below that strike (using daily LOW, not just the closing price — a spread
    can be breached and recover, and using Low is the conservative/correct
    check for whether the short strike was ever actually threatened) over the
    following forward_days trading days?

    Returns: (containment_by_otm: {otm_pct: bool}, max_drawdown_pct: float)
    max_drawdown_pct is the worst intraday drop within the window regardless
    of strike — useful for severity, independent of which OTM level you pick.
    """
    current = daily_df["Close"].iloc[idx]
    forward_lows = daily_df["Low"].iloc[idx + 1: idx + 1 + forward_days].tolist()
    if not forward_lows:
        return None, None
    worst_low = min(forward_lows)
    max_drawdown_pct = (current - worst_low) / current * 100

    containment = {}
    for otm in otm_levels:
        strike = current * (1 - otm)
        containment[otm] = bool(worst_low >= strike)
    return containment, round(max_drawdown_pct, 3)


def run_backtest(spx_ticker: str = "^GSPC", start_date: str = "2018-01-01",
                  forward_days: int = 5, min_history_years: int = 10):
    """
    For each trading day T from start_date onward:
      1. Compute Almanac score using ONLY monthly closes strictly before T
      2. Compute Technical score using ONLY daily closes up to and including T
      3. Compute Macro score using ONLY macro-ticker closes up to and including T
      4. Run the real CompositeScorer on the three mapped scores
      5. Record whether a short put at each OTM level would have been
         breached (via daily LOW) over the following forward_days trading
         days — the metric that actually matches a bull put spread's payoff,
         not a raw average-return metric. See ADR-008/ADR-009.
    No step above uses any data dated after T. That is the entire point.
    """
    daily = download_history(spx_ticker, period="max", interval="1d")
    monthly = download_history(spx_ticker, period="max", interval="1mo")
    macro_daily = {name: download_history(tkr, period="max", interval="1d")
                   for name, tkr in MACRO_TICKERS.items()}

    daily["Date"] = daily["Date"].dt.tz_localize(None)
    monthly["Date"] = monthly["Date"].dt.tz_localize(None)
    for name in macro_daily:
        macro_daily[name]["Date"] = macro_daily[name]["Date"].dt.tz_localize(None)

    monthly_closes_with_dates = [(row["Date"].date(), row["Close"]) for _, row in monthly.iterrows()]

    start = datetime.strptime(start_date, "%Y-%m-%d")
    scorer = CompositeScorer()
    rows = []
    skipped = 0

    daily_dates = daily["Date"].tolist()
    for idx, dt in enumerate(daily_dates):
        if dt < start:
            continue
        if idx + forward_days >= len(daily_dates):
            break  # not enough future data to score this day's outcome

        as_of = dt.date()
        closes_up_to_now = daily["Close"].iloc[:idx + 1].tolist()
        if len(closes_up_to_now) < 210:  # need ~200d for the 200dma, matching technical_agent
            skipped += 1
            continue

        # --- Almanac: monthly stats using only months strictly before as_of ---
        monthly_before = [(d, c) for d, c in monthly_closes_with_dates if d < as_of]
        avg_ret, win_rate, years = compute_monthly_stats(monthly_before, as_of.month)
        if avg_ret is None:
            skipped += 1
            continue
        bias_label = ("bullish" if avg_ret > 0.005 and win_rate > 0.55
                       else "bearish" if avg_ret < -0.005 and win_rate < 0.45
                       else "neutral")
        almanac_out = AlmanacOutput(month=as_of.month, seasonal_bias=bias_label,
                                     avg_return_this_month=avg_ret, win_rate_this_month=win_rate,
                                     years_of_history=years, notes="backtest")

        # --- Technical: RSI/MA using only closes up to and including as_of ---
        current = closes_up_to_now[-1]
        ma50 = sum(closes_up_to_now[-50:]) / 50
        ma200 = sum(closes_up_to_now[-200:]) / 200
        pct_from_50 = (current - ma50) / ma50 * 100
        pct_from_200 = (current - ma200) / ma200 * 100
        rsi = compute_rsi(closes_up_to_now)
        trend = ("uptrend" if pct_from_50 > 1 and pct_from_200 > 1
                  else "downtrend" if pct_from_50 < -1 and pct_from_200 < -1
                  else "sideways")
        technical_out = TechnicalOutput(ticker=spx_ticker, trend=trend, rsi=rsi,
                                         pct_from_50dma=pct_from_50, pct_from_200dma=pct_from_200,
                                         momentum_20d=0.0, notes="backtest")

        # --- Macro: same regime classifier, fed historical data up to as_of ---
        try:
            changes = {}
            for name, hist_df in macro_daily.items():
                sub = hist_df[hist_df["Date"] <= dt]["Close"]
                if len(sub) < 6:
                    raise ValueError("insufficient macro history")
                closes = sub.tolist()
                changes[name] = (closes[-1] - closes[-6]) / closes[-6] * 100
            vix_level = macro_daily["vix"][macro_daily["vix"]["Date"] <= dt]["Close"].tolist()[-1]
            regime = classify_regime(changes["dxy"], changes["yield_10y"], changes["oil"],
                                       changes["gold"], vix_level)
            macro_out = MacroOutput(regime=regime, dxy_change_1w=changes["dxy"],
                                     yield_10y_change_1w=changes["yield_10y"], oil_change_1w=changes["oil"],
                                     gold_change_1w=changes["gold"], vix_level=vix_level, notes="backtest")
        except (ValueError, IndexError, KeyError):
            skipped += 1
            continue

        a = map_almanac(almanac_out)
        m = map_macro(macro_out)
        t = map_technical(technical_out)
        result = scorer.score({"almanac": a, "macro": m, "technical": t})

        containment, max_drawdown = compute_containment(daily, idx, forward_days, OTM_LEVELS)
        if containment is None:
            skipped += 1
            continue

        rows.append({
            "date": as_of.isoformat(),
            "composite_score": result.weighted_score,
            "bias": result.bias.value,
            "containment": {str(k): v for k, v in containment.items()},
            "max_drawdown_pct": max_drawdown,
        })

    return rows, skipped


def summarize(rows):
    """For each bias bucket AND each OTM level, report the containment rate —
    the fraction of days where a short put at that strike would NOT have been
    breached over the holding period. This is the metric that matches the
    actual strategy's payoff (premium collection, not directional capture).
    Every bucket is compared against the unconditional baseline: if LONG-day
    containment isn't meaningfully better than just always selling puts
    regardless of the composite score, the score isn't adding value for THIS
    strategy, even if it looked fine on a raw-return metric.
    """
    buckets = {b.value: [] for b in Bias}
    for r in rows:
        buckets[r["bias"]].append(r)

    def containment_rate(bucket_rows, otm_key):
        vals = [r["containment"][otm_key] for r in bucket_rows if otm_key in r["containment"]]
        return sum(vals) / len(vals) if vals else None

    def avg_drawdown(bucket_rows):
        vals = [r["max_drawdown_pct"] for r in bucket_rows if r["max_drawdown_pct"] == r["max_drawdown_pct"]]
        return round(sum(vals) / len(vals), 3) if vals else None

    otm_keys = [str(x) for x in OTM_LEVELS]
    baseline_containment = {k: containment_rate(rows, k) for k in otm_keys}
    baseline_drawdown = avg_drawdown(rows)

    summary = {}
    for bias, bucket_rows in buckets.items():
        if not bucket_rows:
            summary[bias] = {"n": 0}
            continue
        entry = {"n": len(bucket_rows), "avg_max_drawdown_pct": avg_drawdown(bucket_rows)}
        for k in otm_keys:
            rate = containment_rate(bucket_rows, k)
            entry[f"containment_rate_otm_{k}"] = round(rate, 4) if rate is not None else None
            entry[f"edge_vs_baseline_otm_{k}"] = (
                round(rate - baseline_containment[k], 4)
                if rate is not None and baseline_containment[k] is not None else None
            )
        summary[bias] = entry

    summary["_baseline_unconditional"] = {
        "n": len(rows),
        "avg_max_drawdown_pct": baseline_drawdown,
        **{f"containment_rate_otm_{k}": round(v, 4) if v is not None else None
           for k, v in baseline_containment.items()},
        "note": "Containment rate if you sold this spread on EVERY day regardless "
                 "of the composite score. Every bucket's containment_rate above "
                 "should be compared against this, and 'edge_vs_baseline' already "
                 "does that subtraction for you — a positive edge means the "
                 "composite score is actually adding value for THIS strategy; "
                 "near-zero or negative means it isn't, no matter how it looked "
                 "on a raw-return metric.",
    }
    return summary


if __name__ == "__main__":
    import traceback
    try:
        rows, skipped = run_backtest()
        summary = summarize(rows)
        output = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "success",
            "methodology": "Point-in-time: Almanac uses only months strictly before "
                            "the test date; Technical/Macro use only prices up to and "
                            "including the test date. Outcome metric is STRIKE "
                            "CONTAINMENT, not raw return — for each of "
                            f"{OTM_LEVELS}, was daily LOW ever below "
                            "spot*(1-otm) over the next 5 trading days. This matches "
                            "a bull put spread's actual payoff (premium collection, "
                            "not directional capture). Fundamental Agent and LLM "
                            "synthesis NOT included (no point-in-time-safe historical "
                            "source wired up yet). See ADR-009.",
            "rows_scored": len(rows),
            "rows_skipped_insufficient_data": skipped,
            "summary_by_bias": summary,
        }
        print(json.dumps(output, indent=2))
        with open("backtest_result.json", "w") as f:
            json.dump({**output, "daily_rows": rows}, f, indent=2)
    except Exception as e:
        error_output = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "error",
            "error_type": type(e).__name__,
            "error_message": str(e),
            "traceback": traceback.format_exc(),
        }
        print(json.dumps(error_output, indent=2))
        with open("backtest_result.json", "w") as f:
            json.dump(error_output, f, indent=2)
        raise  # still fail the workflow step loudly, just after leaving a diagnosable trail
