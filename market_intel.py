"""
market_intel.py — CP3405 Market Intelligence Signal Integration
Connects almanac, macro, technical, LLM synthesis and human scores
to produce a composite directional bias score for trade decisions.
"""

import json
import datetime
import urllib.request


def get_vix():
    """Fetch current VIX level via yfinance-compatible endpoint."""
    try:
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "-c",
             "import yfinance as yf; v=yf.Ticker('^VIX').fast_info; print(v['lastPrice'])"],
            capture_output=True, text=True, timeout=10
        )
        return float(result.stdout.strip())
    except Exception:
        return None


def get_almanac_score():
    """
    Seasonal almanac bias for SPX.
    Based on monthly seasonality and day-of-week patterns.
    Returns score 0-100 (>55 = bullish lean, <45 = bearish lean)
    """
    today = datetime.date.today()
    month = today.month
    dow   = today.weekday()  # 0=Monday

    # Monthly seasonality scores (historical SPX bias)
    monthly_bias = {
        1: 62, 2: 55, 3: 60, 4: 68, 5: 52,
        6: 55, 7: 63, 8: 48, 9: 42, 10: 53,
        11: 68, 12: 70
    }

    # Monday tends to be weakest day for opens
    dow_adjustment = {0: -3, 1: 0, 2: 2, 3: 1, 4: -1}

    base = monthly_bias.get(month, 55)
    adj  = dow_adjustment.get(dow, 0)
    return min(100, max(0, base + adj))


def get_macro_score():
    """
    Macro environment score based on Fed stance, yields, credit spreads.
    In production: fetch from FRED API or news sentiment.
    Default: neutral 55 until live data feed connected.
    """
    # TODO: Connect to FRED API for live macro data
    # Placeholder returns neutral-bullish
    return 57


def get_technical_score(symbol="SPX"):
    """
    Technical analysis score based on price action vs key MAs.
    In production: fetch from IBKR market data.
    """
    # TODO: Connect to IBKR API for live price data
    # Placeholder returns neutral
    return 58


def get_llm_synthesis_score():
    """
    LLM consensus score from CP3405 agent framework.
    Aggregates ChatGPT, Gemini, DeepSeek predictions.
    In production: reads from CP3405 prediction submission.
    """
    # TODO: Connect to CP3405 prediction pipeline
    # Placeholder returns neutral-bullish
    return 60


def get_human_score():
    """
    Professor Dr. Tan's human override score.
    Read from trade_state.json — set manually via website.
    """
    try:
        with open("docs/data/trade_state.json", "r") as f:
            state = json.load(f)
            return state.get("human_score", 55)
    except Exception:
        return 55


def get_composite_score():
    """
    Weighted composite of all five agent scores.
    Returns dict with individual scores and composite.
    """
    scores = {
        "almanac":   get_almanac_score(),
        "macro":     get_macro_score(),
        "technical": get_technical_score(),
        "llm":       get_llm_synthesis_score(),
        "human":     get_human_score()
    }

    # Weights — human score carries most weight as override
    weights = {
        "almanac":   0.15,
        "macro":     0.20,
        "technical": 0.25,
        "llm":       0.20,
        "human":     0.20
    }

    composite = sum(scores[k] * weights[k] for k in scores)

    return {
        "scores":    scores,
        "composite": round(composite, 1),
        "bias":      "BULLISH" if composite >= 55 else
                     "BEARISH" if composite <= 45 else "NEUTRAL",
        "timestamp": datetime.datetime.utcnow().isoformat()
    }


if __name__ == "__main__":
    result = get_composite_score()
    print(json.dumps(result, indent=2))
