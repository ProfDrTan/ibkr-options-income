"""
market_data.py - Live SPX price fetch
Prof Dr Tan | github.com/ProfDrTan/ibkr-options-income

Provides get_spx_price() for strategy.py / main.py. Tries two independent
free public sources in order, then falls back to the last known price
already stored in data/trade_state.json if both fail — and always reports
which path was used, so a stale/fallback price is never silently mistaken
for a live one.
"""
import json
import urllib.request
import urllib.error


def _fetch_yahoo():
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?interval=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())
    price = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
    return float(price)


def _fetch_stooq():
    url = "https://stooq.com/q/l/?s=%5Espx&f=sd2t2ohlcv&h&e=csv"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        text = r.read().decode()
    lines = text.strip().split("\n")
    header = lines[0].split(",")
    row = lines[1].split(",")
    close_idx = header.index("Close")
    price = row[close_idx]
    if price in ("N/D", ""):
        raise ValueError("Stooq returned no data (N/D)")
    return float(price)


def _last_known_price(state_path="data/trade_state.json"):
    try:
        with open(state_path) as f:
            state = json.load(f)
        price = state.get("recommendation", {}).get("spx_price")
        if price:
            return float(price)
    except Exception:
        pass
    return None


def get_spx_price(state_path="data/trade_state.json"):
    """
    Returns (price, source) where source is one of:
    'yahoo', 'stooq', 'last_known_stale', or raises RuntimeError if all fail.
    """
    try:
        return _fetch_yahoo(), "yahoo"
    except Exception as e:
        print(f"  SPX price: Yahoo fetch failed ({e}), trying Stooq...")
    try:
        return _fetch_stooq(), "stooq"
    except Exception as e:
        print(f"  SPX price: Stooq fetch failed ({e}), trying last known...")
    last = _last_known_price(state_path)
    if last:
        print(f"  SPX price: WARNING — using last known price {last} (both live sources failed, this is stale)")
        return last, "last_known_stale"
    raise RuntimeError("SPX price: all sources failed (Yahoo, Stooq) and no last-known price exists in state file")


if __name__ == "__main__":
    price, source = get_spx_price()
    print(f"SPX price: {price} (source: {source})")
