"""
hourly_market_snapshot.py — Hourly market status + position exposure snapshot
sent to Telegram, combining Almanac/Macro/Technical/Fundamental agent signals
with live price data and Prof Dr. Tan's manually-logged position state.

Design notes (see README section "Hourly Snapshot" for full rationale):
- Almanac/Macro/Technical (market-prediction-engine) and Fundamental (this
  repo's bot_core) all run on DAILY-frequency underlying data. Recomputing
  them every hour would burn API calls for zero new signal, so they are
  computed once per UTC calendar day and cached in data/daily_signal_cache.json.
  Every hourly message states the cache's as-of date plainly rather than
  implying hour-fresh macro/technical data that doesn't exist.
- Live-fresh components each run: NQ/ES/MNQ price (Yahoo Finance, ~15-20min
  delayed — same caveat as the existing yield/price alert scripts) and the
  position P&L math (live price x last-logged qty/entry from position_state.json).
- position_state.json is NOT auto-derived from any broker API — there is no
  persistent IBKR gateway session or Schwab/TOS API wired up for this repo.
  It is updated by Claude in chat whenever Prof Dr. Tan sends a TOS screenshot.
  If it goes stale, the message says so explicitly rather than silently
  computing P&L against an out-of-date position.
- No weekend sends: skips Saturday and Sunday (UTC) entirely, since futures
  P&L/decisions aren't actionable those days per standing instruction.
"""
import os
import sys
import json
import datetime
from zoneinfo import ZoneInfo
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
DATA_DIR = REPO_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

CACHE_FILE = DATA_DIR / "daily_signal_cache.json"
POSITION_FILE = DATA_DIR / "position_state.json"
SGT = ZoneInfo("Asia/Singapore")

# Cross-repo checkout paths (set up by the GitHub Actions workflow).
# Each agent module lives in its own subfolder (agents/macro/macro_agent.py
# etc.) and internally does sys.path.insert(parents[1]) to reach schemas.py
# in agents/ — so THIS path needs to point at each subfolder directly for
# `import macro_agent` etc. to resolve, not at agents/ itself.
MPE_PATH = REPO_ROOT / "market-prediction-engine"
for sub in ("macro", "technical", "almanac", "events"):
    sys.path.insert(0, str(MPE_PATH / "agents" / sub))
sys.path.insert(0, str(REPO_ROOT / "bot_core"))

# futures-alert-monitor checkout (private repo, needs CROSS_REPO_PAT to clone
# in the workflow) — source of the daily support/resistance/POC/VAH/VAL
# levels, which ARE genuinely recalculated daily (cron 13:00 UTC, before US
# open) via that repo's own calculate_levels.py. NOT the same as
# indicators_NQ.json in that repo, which has no cron wired to it at all and
# would be stale indefinitely if trusted — so that file is deliberately not
# read here; the intraday technical read below is computed fresh instead.
FAM_PATH = REPO_ROOT / "futures-alert-monitor"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
FRED_API_KEY = os.environ.get("FRED_API_KEY")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")

# Yahoo tickers for live-ish price (delayed ~15-20min, same as check_price.py)
LIVE_TICKERS = {"NQ": "NQ=F", "ES": "ES=F", "MNQ": "NQ=F", "MES": "ES=F",
                 "GC": "GC=F", "MGC": "GC=F"}

# Futures contract multipliers (USD per point / per $1 move)
# MGC (Micro Gold) = 10 troy oz/contract -> $10 per $1/oz move.
MULTIPLIERS = {"NQ": 20.0, "MNQ": 2.0, "ES": 50.0, "MES": 5.0,
                "GC": 100.0, "MGC": 10.0}


def load_json(path, default=None):
    if not path.exists():
        return default
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def fetch_live_price(yahoo_symbol):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}"
           f"?interval=1m&range=1d")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
    meta = data["chart"]["result"][0]["meta"]
    current = meta["regularMarketPrice"]
    prior_close = meta.get("previousClose", current)
    return current, prior_close


def compute_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d for d in deltas[-period:] if d > 0]
    losses = [-d for d in deltas[-period:] if d < 0]
    avg_gain = sum(gains) / period if gains else 0.0
    avg_loss = sum(losses) / period if losses else 0.0
    if avg_gain == 0 and avg_loss == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def ema_series(closes, period):
    k = 2 / (period + 1)
    ema = [closes[0]]
    for price in closes[1:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return ema


def fetch_intraday_technical(yahoo_symbol):
    """Genuinely hour-fresh technical read: 5-min bars for today, EMA8/21
    cross + RSI14 computed on the spot. Deliberately NOT read from
    futures-alert-monitor's indicators_NQ.json, which has no cron running it
    and would silently go stale forever if trusted as live."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}"
           f"?interval=5m&range=1d")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
    result = data["chart"]["result"][0]
    closes = [c for c in result["indicators"]["quote"][0]["close"] if c is not None]
    if len(closes) < 22:
        return None
    ema8 = ema_series(closes, 8)
    ema21 = ema_series(closes, 21)
    prev_diff = ema8[-2] - ema21[-2]
    curr_diff = ema8[-1] - ema21[-1]
    if prev_diff <= 0 and curr_diff > 0:
        cross = "golden_cross_just_occurred"
    elif prev_diff >= 0 and curr_diff < 0:
        cross = "death_cross_just_occurred"
    else:
        cross = "bullish_bias" if curr_diff > 0 else "bearish_bias"
    return {"rsi14_intraday": compute_rsi(closes), "ema_cross_state_intraday": cross}


def fetch_daily_levels(symbol="NQ"):
    """Reads the daily support/resistance/POC/VAH/VAL from
    futures-alert-monitor's levels_NQ.json, checked out by the workflow.
    Legitimately daily (cron 13:00 UTC before US open) -- not pretended
    hourly-fresh."""
    path = FAM_PATH / f"levels_{symbol}.json"
    return load_json(path, default=None)


def compute_daily_signals():
    """Runs Almanac/Macro/Technical (market-prediction-engine) + Fundamental
    (this repo). Any agent that errors is excluded, not faked — matches the
    CompositeScorer's existing 'missing agent' handling."""
    results = {}

    # --- Macro / Technical / Almanac (market-prediction-engine) ---
    # NOTE: these return typed domain objects (MacroOutput/TechnicalOutput/
    # AlmanacOutput), NOT a normalized -100..+100 score like AgentOutput —
    # only Fundamental below extends AgentOutput. Store the raw dataclass
    # fields as-is; build_message() renders each type's actual fields rather
    # than pretending a uniform "score" exists across all four.
    try:
        import macro_agent
        m = macro_agent.run()
        results["macro"] = m.__dict__ if m else None
    except Exception as e:
        results["macro"] = {"error": str(e)}

    try:
        import technical_agent
        t = technical_agent.run("NQ=F")
        results["technical"] = t.__dict__ if t else None
    except Exception as e:
        results["technical"] = {"error": str(e)}

    try:
        import almanac_agent
        a = almanac_agent.run("NQ=F")
        a_dict = a.__dict__ if a else None
        if a_dict:
            a_dict["chewable_summary"] = almanac_agent.chewable_summary(a, "NQ")
        results["almanac"] = a_dict
    except Exception as e:
        results["almanac"] = {"error": str(e)}

    try:
        import events_agent
        ev = events_agent.run()
        results["events"] = ev.__dict__ if ev else None
    except Exception as e:
        results["events"] = {"error": str(e)}

    # --- Fundamental (this repo, bot_core) ---
    try:
        from agents.fundamental.agent import FundamentalAgent
        fa = FundamentalAgent(fred_api_key=FRED_API_KEY)
        fo = fa.run(context={})
        results["fundamental"] = {
            "score": fo.score, "confidence": fo.confidence,
            "rationale": fo.rationale, "stale": fo.stale,
        }
    except Exception as e:
        results["fundamental"] = {"error": str(e)}

    results["as_of_date"] = datetime.date.today().isoformat()
    return results


def get_daily_signals():
    cache = load_json(CACHE_FILE, default={})
    today = datetime.date.today().isoformat()
    if cache.get("as_of_date") == today:
        return cache, False  # cache hit, not recomputed
    fresh = compute_daily_signals()
    save_json(CACHE_FILE, fresh)
    return fresh, True


def _pnl_for_one(pos):
    """Computes P&L note for a single position dict. Returns (pnl, note)."""
    symbol = pos.get("symbol")
    qty = pos.get("qty")  # negative = short
    entry = pos.get("entry_price")
    logged_at = pos.get("logged_at", "unknown")

    if symbol not in LIVE_TICKERS or qty is None or entry is None:
        return None, f"Position file incomplete (logged {logged_at})."

    try:
        current, _ = fetch_live_price(LIVE_TICKERS[symbol])
    except Exception as e:
        return None, f"Price fetch failed for {symbol}: {e}"

    mult = MULTIPLIERS.get(symbol, 1.0)
    pnl = qty * mult * (current - entry)
    direction = "short" if qty < 0 else "long"
    note = (f"{abs(qty):.0f} {symbol} {direction} @ {entry:,.2f}, "
            f"now {current:,.2f} -> P&L {'+'if pnl>=0 else ''}{pnl:,.0f} USD "
            f"(position logged {logged_at})")
    return pnl, note


def get_position_pnl():
    """Reads data/position_state.json. Supports EITHER a single position dict
    (legacy format) OR a list of position dicts (current format, as of 19 Aug
    2026 when the MGC gold position was added alongside the MNQ short) — each
    entry tracked and reported independently, since they're unrelated trades.
    Returns (list_of_pnl, combined_note_string)."""
    raw = load_json(POSITION_FILE, default=None)
    if not raw:
        return [], "No position on file — send a TOS screenshot to log one."

    positions = raw if isinstance(raw, list) else [raw]
    pnls, notes = [], []
    for pos in positions:
        pnl, note = _pnl_for_one(pos)
        pnls.append(pnl)
        notes.append(note)
    combined_note = "\n  ".join(notes)
    return pnls, combined_note


def llm_synthesis(signals, position_note, levels, intraday):
    """One cheap DeepSeek call for a short, decision-oriented readout —
    explicitly asked for a level to watch and an invalidation point, tied to
    the actual logged position, not a generic market comment."""
    if not DEEPSEEK_API_KEY:
        return None
    prompt = (
        "You are a terse trading-desk assistant. Given the data below, answer "
        "in exactly this structure, max 55 words total:\n"
        "BIAS: [one line]\nWATCH LEVEL: [specific price + why]\n"
        "INVALIDATION: [specific price that would flip the bias]\n"
        "ACTION: [one line tied to the CURRENT position given, not generic]\n\n"
        f"Daily signals (as of {signals.get('as_of_date')}): {json.dumps(signals)}\n"
        f"Intraday technical (fresh this hour): {json.dumps(intraday)}\n"
        f"Daily support/resistance/POC levels: {json.dumps(levels)}\n"
        f"Current position: {position_note}\n"
        "No disclaimers, no restating numbers verbatim, just the structured read."
    )
    try:
        req = urllib.request.Request(
            "https://api.deepseek.com/v1/chat/completions",
            data=json.dumps({
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 150,
                "temperature": 0.3,
            }).encode(),
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                     "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"(LLM synthesis unavailable: {e})"


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Missing Telegram credentials, skipping send.")
        print(message)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": message}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10)


def build_message(signals, was_recomputed, live_prices, pnl, position_note,
                   intraday, levels, synthesis):
    lines = []
    now_sgt = datetime.datetime.now(SGT).strftime("%Y-%m-%d %H:%M SGT")
    lines.append(f"MARKET SNAPSHOT — {now_sgt}")
    lines.append("")
    lines.append("Live price (Yahoo, ~15-20min delayed):")
    for sym, (cur, prior) in live_prices.items():
        chg = cur - prior
        lines.append(f"  {sym}: {cur:,.2f} ({'+' if chg>=0 else ''}{chg:,.2f})")
    lines.append("")
    lines.append("Position:")
    lines.append(f"  {position_note}")
    lines.append("")

    if intraday:
        lines.append("Intraday technical (fresh this hour, NQ 5m bars):")
        lines.append(f"  RSI14: {intraday.get('rsi14_intraday')}, "
                      f"EMA8/21: {intraday.get('ema_cross_state_intraday')}")
        lines.append("")

    if levels:
        lines.append(f"Daily NQ levels (as of last recalc, ~13:00 UTC):")
        lines.append(f"  Support {levels.get('support')} / Resistance {levels.get('resistance')}")
        lines.append(f"  POC {levels.get('poc')} / VAH {levels.get('vah')} / VAL {levels.get('val')}")
        lines.append("")

    tag = "recomputed today" if was_recomputed else "cached from earlier today"
    lines.append(f"Daily signals (as of {signals.get('as_of_date')}, {tag}):")

    m = signals.get("macro")
    if isinstance(m, dict) and "error" not in m:
        lines.append(f"  Macro: {m.get('regime')} (10Y {m.get('yield_10y_change_1w'):+.2f}% wk, "
                      f"VIX {m.get('vix_level')})")
    else:
        lines.append("  Macro: unavailable")

    t = signals.get("technical")
    if isinstance(t, dict) and "error" not in t:
        lines.append(f"  Technical (daily): {t.get('trend')}, RSI {t.get('rsi')}, "
                      f"{t.get('pct_from_50dma'):+.1f}% from 50dma")
    else:
        lines.append("  Technical (daily): unavailable")

    a = signals.get("almanac")
    if isinstance(a, dict) and "error" not in a:
        chewable = a.get("chewable_summary")
        if chewable:
            lines.append(f"  Almanac: {chewable}")
        else:
            lines.append(f"  Almanac: {a.get('seasonal_bias')} seasonality, "
                          f"{a.get('win_rate_this_month', 0)*100:.0f}% win rate "
                          f"over {a.get('years_of_history')}y")
    else:
        lines.append("  Almanac: unavailable")

    f = signals.get("fundamental")
    if isinstance(f, dict) and "error" not in f and not f.get("stale"):
        lines.append(f"  Fundamental: score {f.get('score'):+.1f} "
                      f"(confidence {f.get('confidence')})")
    else:
        lines.append("  Fundamental: unavailable")

    ev = signals.get("events")
    if isinstance(ev, dict) and "error" not in ev and ev.get("chewable_summary"):
        lines.append("")
        lines.append("This week's catalysts:")
        lines.append(ev["chewable_summary"])

    if synthesis:
        lines.append("")
        lines.append(synthesis)
    return "\n".join(lines)


def main():
    today = datetime.date.today()
    if today.weekday() in (5, 6):  # Sat, Sun — no weekend sends
        print("Weekend — skipping send.")
        return

    signals, was_recomputed = get_daily_signals()

    live_prices = {}
    for sym in ("NQ", "ES", "GC"):
        try:
            cur, prior = fetch_live_price(LIVE_TICKERS[sym])
            live_prices[sym] = (cur, prior)
        except Exception as e:
            print(f"Price fetch failed for {sym}: {e}")

    try:
        intraday = fetch_intraday_technical(LIVE_TICKERS["NQ"])
    except Exception as e:
        print(f"Intraday technical fetch failed: {e}")
        intraday = None

    levels = fetch_daily_levels("NQ")

    pnl, position_note = get_position_pnl()
    synthesis = llm_synthesis(signals, position_note, levels, intraday)

    message = build_message(signals, was_recomputed, live_prices, pnl,
                             position_note, intraday, levels, synthesis)
    print(message)
    send_telegram(message)


if __name__ == "__main__":
    main()

