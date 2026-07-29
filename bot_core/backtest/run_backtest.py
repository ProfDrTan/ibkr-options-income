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


def run_backtest(spx_ticker: str = "^GSPC", start_date: str = "2018-01-01",
                  forward_days: int = 5, min_history_years: int = 10):
    """
    For each trading day T from start_date onward:
      1. Compute Almanac score using ONLY monthly closes strictly before T
      2. Compute Technical score using ONLY daily closes up to and including T
      3. Compute Macro score using ONLY macro-ticker closes up to and including T
      4. Run the real CompositeScorer on the three mapped scores
      5. Record the actual forward return from T to T+forward_days trading days
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

        forward_return = (daily_dates_close(daily, idx + forward_days) - current) / current * 100

        rows.append({
            "date": as_of.isoformat(),
            "composite_score": result.weighted_score,
            "bias": result.bias.value,
            "forward_return_pct": round(forward_return, 3),
        })

    return rows, skipped


def daily_dates_close(daily_df, idx):
    return daily_df["Close"].iloc[idx]


def summarize(rows):
    buckets = {b.value: [] for b in Bias}
    for r in rows:
        buckets[r["bias"]].append(r["forward_return_pct"])

    # NaN can appear at the edges of the historical series (a data-provider
    # gap near the most recent date) — exclude rather than let it silently
    # poison an average via NaN propagation.
    def clean(returns):
        return [x for x in returns if x == x]  # x != x is the NaN check

    all_returns_clean = clean([r["forward_return_pct"] for r in rows])
    baseline_avg = sum(all_returns_clean) / len(all_returns_clean) if all_returns_clean else None
    baseline_pct_positive = (sum(1 for x in all_returns_clean if x > 0) / len(all_returns_clean)
                               if all_returns_clean else None)

    summary = {}
    for bias, returns in buckets.items():
        returns_clean = clean(returns)
        excluded_nan = len(returns) - len(returns_clean)
        if not returns_clean:
            summary[bias] = {"n": 0, "excluded_nan": excluded_nan}
            continue
        avg = sum(returns_clean) / len(returns_clean)
        hit_rate = None
        if bias == "long":
            hit_rate = sum(1 for x in returns_clean if x > 0) / len(returns_clean)
        elif bias == "short":
            hit_rate = sum(1 for x in returns_clean if x < 0) / len(returns_clean)
        summary[bias] = {
            "n": len(returns_clean),
            "excluded_nan": excluded_nan,
            "avg_forward_return_pct": round(avg, 3),
            "hit_rate": round(hit_rate, 3) if hit_rate is not None else None,
            "edge_vs_baseline_pct": round(avg - baseline_avg, 3) if baseline_avg is not None else None,
        }

    summary["_baseline_unconditional"] = {
        "n": len(all_returns_clean),
        "avg_forward_return_pct": round(baseline_avg, 3) if baseline_avg is not None else None,
        "pct_days_positive": round(baseline_pct_positive, 3) if baseline_pct_positive is not None else None,
        "note": "Average 5-day forward return across ALL scored days regardless "
                 "of bias — this is what 'just being in the market' looks like "
                 "over the same window. Compare every bucket above against this, "
                 "not against zero.",
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
                            "including the test date. Forward return measured 5 trading "
                            "days ahead. Fundamental Agent and LLM synthesis NOT included "
                            "(no point-in-time-safe historical source wired up yet).",
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
