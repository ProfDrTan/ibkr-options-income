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
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
DATA_DIR = REPO_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

CACHE_FILE = DATA_DIR / "daily_signal_cache.json"
POSITION_FILE = DATA_DIR / "position_state.json"

# Cross-repo checkout paths (set up by the GitHub Actions workflow).
# Each agent module lives in its own subfolder (agents/macro/macro_agent.py
# etc.) and internally does sys.path.insert(parents[1]) to reach schemas.py
# in agents/ — so THIS path needs to point at each subfolder directly for
# `import macro_agent` etc. to resolve, not at agents/ itself.
MPE_PATH = REPO_ROOT / "market-prediction-engine"
for sub in ("macro", "technical", "almanac"):
    sys.path.insert(0, str(MPE_PATH / "agents" / sub))
sys.path.insert(0, str(REPO_ROOT / "bot_core"))

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
FRED_API_KEY = os.environ.get("FRED_API_KEY")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")

# Yahoo tickers for live-ish price (delayed ~15-20min, same as check_price.py)
LIVE_TICKERS = {"NQ": "NQ=F", "ES": "ES=F", "MNQ": "NQ=F", "MES": "ES=F"}

# Futures contract multipliers (USD per point)
MULTIPLIERS = {"NQ": 20.0, "MNQ": 2.0, "ES": 50.0, "MES": 5.0}


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
        results["almanac"] = a.__dict__ if a else None
    except Exception as e:
        results["almanac"] = {"error": str(e)}

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


def get_position_pnl():
    pos = load_json(POSITION_FILE, default=None)
    if not pos:
        return None, "No position on file — send a TOS screenshot to log one."

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


def llm_synthesis(signals, position_note):
    """One cheap DeepSeek call for a short actionable readout. Skipped
    entirely (not faked) if no key is configured."""
    if not DEEPSEEK_API_KEY:
        return None
    prompt = (
        "You are a terse trading-desk assistant. In 2 short sentences, max "
        "40 words total, give an actionable readout for NQ/MNQ futures given:\n"
        f"Daily signals (as of {signals.get('as_of_date')}): {json.dumps(signals)}\n"
        f"Current position: {position_note}\n"
        "No disclaimers, no restating the numbers verbatim, just the read."
    )
    try:
        req = urllib.request.Request(
            "https://api.deepseek.com/v1/chat/completions",
            data=json.dumps({
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 120,
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


def build_message(signals, was_recomputed, live_prices, pnl, position_note, synthesis):
    lines = []
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"MARKET SNAPSHOT — {now}")
    lines.append("")
    lines.append("Live price (Yahoo, ~15-20min delayed):")
    for sym, (cur, prior) in live_prices.items():
        chg = cur - prior
        lines.append(f"  {sym}: {cur:,.2f} ({'+' if chg>=0 else ''}{chg:,.2f})")
    lines.append("")
    lines.append(f"Position: {position_note}")
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
        lines.append(f"  Technical: {t.get('trend')}, RSI {t.get('rsi')}, "
                      f"{t.get('pct_from_50dma'):+.1f}% from 50dma")
    else:
        lines.append("  Technical: unavailable")

    a = signals.get("almanac")
    if isinstance(a, dict) and "error" not in a:
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
    if synthesis:
        lines.append("")
        lines.append(f"Read: {synthesis}")
    return "\n".join(lines)


def main():
    today = datetime.date.today()
    if today.weekday() in (5, 6):  # Sat, Sun — no weekend sends
        print("Weekend — skipping send.")
        return

    signals, was_recomputed = get_daily_signals()

    live_prices = {}
    for sym in ("NQ", "ES"):
        try:
            cur, prior = fetch_live_price(LIVE_TICKERS[sym])
            live_prices[sym] = (cur, prior)
        except Exception as e:
            print(f"Price fetch failed for {sym}: {e}")

    pnl, position_note = get_position_pnl()
    synthesis = llm_synthesis(signals, position_note)

    message = build_message(signals, was_recomputed, live_prices, pnl, position_note, synthesis)
    print(message)
    send_telegram(message)


if __name__ == "__main__":
    main()
