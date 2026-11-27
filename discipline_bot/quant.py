"""
discipline_bot/quant.py: Quant book #1, ETF time-series momentum (paper account), started 27 Nov 2026.

Rules (research/tsmom.py run2, tested 2007-2026 on daily ETF data):
  - Universe: 14 ETFs across US/intl stocks, bonds, credit, gold, silver, oil, dollar, real estate.
  - Hold an ETF only if its 12-month total return is positive (long or flat, never short).
  - Size by inverse volatility (60-day), whole book scaled toward 15% annual volatility, max 25% in any one ETF,
    never more than 100% invested (no leverage). The rest stays in cash.
  - Rebalance once a week (first US session hour after 10:00 New York each week), market orders.
  - Test result: about 4.3% a year, worst fall -12.9%, positive in both halves (Sharpe 0.52 / 0.75).
Sleeve capital is fixed at QUANT_CAPITAL; the sleeve keeps its own cash ledger so its P&L is tracked separately
from the futures bot. Journal: data/quant/.
"""
import csv, datetime as dt, json, math
from pathlib import Path
from zoneinfo import ZoneInfo

from ib_async import Stock, MarketOrder

ROOT = Path(__file__).resolve().parent.parent
QDIR = ROOT / "data" / "quant"
UNIV = ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "HYG", "LQD", "GLD", "SLV", "USO", "UUP", "VNQ"]
QUANT_CAPITAL = 100_000.0
TVOL, CAP, LOOKBACK, VOLSPAN = 0.15, 0.25, 252, 60
REF = "quant_tsmom"
NY = ZoneInfo("America/New_York")


def _ewm_std(x, span):
    a = 2 / (span + 1); m = v = None; out = []
    for r in x:
        if m is None:
            m, v = r, 0.0
        else:
            d = r - m
            m += a * d
            v = (1 - a) * (v + a * d * d)
        out.append(math.sqrt(v) if v is not None else float("nan"))
    return out


def targets(closes):
    """closes: {sym: [daily adjusted closes, oldest first]} -> {sym: weight}"""
    raw = {}
    rets = {}
    for s, c in closes.items():
        if len(c) < LOOKBACK + VOLSPAN:
            continue
        r = [c[i] / c[i - 1] - 1 for i in range(1, len(c))]
        rets[s] = r
        if c[-1] / c[-1 - LOOKBACK] - 1 <= 0:
            continue
        vol = _ewm_std(r, VOLSPAN)[-1] * math.sqrt(252)
        if vol > 0:
            raw[s] = 1 / vol
    n = max(len(rets), 1)
    w = {s: v / n * TVOL for s, v in raw.items()}
    # scale the book toward target vol using trailing portfolio vol of these weights
    k = min(len(r) for r in rets.values()) if rets else 0
    port = [sum(w.get(s, 0) * rets[s][i] for s in rets) for i in range(k - 250, k)] if k > 250 else []
    if port:
        pv = _ewm_std(port, VOLSPAN)[-1] * math.sqrt(252)
        if pv > 0:
            scale = min(TVOL / pv, 1.5)
            w = {s: x * scale for s, x in w.items()}
    w = {s: min(x, CAP) for s, x in w.items()}
    tot = sum(w.values())
    if tot > 1:
        w = {s: x / tot for s, x in w.items()}
    return w


def load_state():
    p = QDIR / "state.json"
    return json.loads(p.read_text()) if p.exists() else {"cash": QUANT_CAPITAL, "shares": {}, "last_week": None}


def save_state(st):
    QDIR.mkdir(parents=True, exist_ok=True)
    (QDIR / "state.json").write_text(json.dumps(st, indent=2))


def _append(name, header, row):
    QDIR.mkdir(parents=True, exist_ok=True)
    p = QDIR / name
    new = not p.exists()
    with p.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerow(row)


def due(st):
    t = dt.datetime.now(NY)
    week = f"{t.isocalendar()[0]}-W{t.isocalendar()[1]:02d}"
    in_hour = t.weekday() < 5 and (10 * 60) <= t.hour * 60 + t.minute < (12 * 60 + 30)
    return in_hour and st.get("last_week") != week, week


def rebalance(ib, log_event, send_telegram, force=False):
    if not all(a.startswith("DU") for a in ib.managedAccounts()):
        return            # 27 Nov 2026: the $100k ETF quant book stays on PAPER only, even when the futures bot goes live
    st = load_state()
    ok, week = due(st)
    if not ok and not force:
        return
    closes, last, cons = {}, {}, {}
    for s in UNIV:
        c = Stock(s, "SMART", "USD")
        ib.qualifyContracts(c)
        cons[s] = c
        bars = ib.reqHistoricalData(c, endDateTime="", durationStr="2 Y", barSizeSetting="1 day",
                                    whatToShow="ADJUSTED_LAST", useRTH=True, formatDate=1)
        closes[s] = [b.close for b in bars]
        tr = ib.reqHistoricalData(c, endDateTime="", durationStr="1 D", barSizeSetting="1 min",
                                  whatToShow="TRADES", useRTH=True, formatDate=1)
        last[s] = tr[-1].close if tr else (bars[-1].close if bars else None)
    w = targets(closes)
    value = st["cash"] + sum(n * (last.get(s) or 0) for s, n in st["shares"].items())
    lines = []
    for s in UNIV:
        px = last.get(s)
        if not px:
            continue
        have = st["shares"].get(s, 0)
        want = int(value * w.get(s, 0) / px)
        d = want - have
        if d == 0:
            continue
        o = MarketOrder("BUY" if d > 0 else "SELL", abs(d))
        o.orderRef = REF
        o.tif = "DAY"
        t = ib.placeOrder(cons[s], o)
        for _ in range(30):
            ib.sleep(1)
            if t.orderStatus.status in ("Filled", "Cancelled", "Inactive"):
                break
        filled = int(t.orderStatus.filled or 0)
        fpx = t.orderStatus.avgFillPrice or px
        if filled:
            sgn = 1 if d > 0 else -1
            st["shares"][s] = have + sgn * filled
            st["cash"] -= sgn * filled * fpx + 1.0          # ~$1 commission per order
            _append("fills.csv", ["time_utc", "week", "symbol", "side", "shares", "price"],
                    [dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), week, s,
                     "BUY" if d > 0 else "SELL", filled, fpx])
            lines.append(f"{'🟢 BUY' if d > 0 else '🔴 SELL'} {filled} {s} @ {fpx:.2f}")
        else:
            lines.append(f"⚠️ {s}: order {t.orderStatus.status}")
            log_event("quant_order", s, f"{d} not filled: {t.orderStatus.status}")
    st["shares"] = {s: n for s, n in st["shares"].items() if n}
    st["last_week"] = week
    value = st["cash"] + sum(n * (last.get(s) or 0) for s, n in st["shares"].items())
    st["value"] = round(value, 2)
    save_state(st)
    _append("equity.csv", ["time_utc", "value", "cash"], [dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                                          round(value, 2), round(st["cash"], 2)])
    held = ", ".join(f"{s} {w[s]:.0%}" for s in sorted(w, key=w.get, reverse=True)) or "all cash"
    log_event("quant_rebalance", "", f"{week}: {held}")
    send_telegram(f"📐 <b>Quant book (ETF trend) weekly rebalance</b> · {week}\n"
                  f"Holding (12-month trend up, sized by volatility): {held}\n"
                  + ("\n".join(lines) if lines else "No changes needed.")
                  + f"\nBook value ${value:,.0f} (started ${QUANT_CAPITAL:,.0f})", html=True)


def mark(ib):
    """Daily value of the sleeve for /quant and the journal."""
    st = load_state()
    px = {}
    for it in ib.portfolio():
        if it.contract.secType == "STK" and it.contract.symbol in st["shares"]:
            px[it.contract.symbol] = it.marketPrice
    value = st["cash"] + sum(n * px.get(s, 0) for s, n in st["shares"].items())
    return st, px, value
