"""
discipline_bot/bot.py — exits decided before emotion gets a vote.

What it does, every LOOP_SECONDS:
  1. git pull, read commands/queue.json (entries you ask for, from the CLI or via Claude committing a command)
  2. every entry passes the guardrails, then goes in as ONE bracket: market entry + IBKR TRAIL stop
     (initial stop 1.5 x ATR, trails at 3 x ATR, tightens only; the stop lives on IBKR's servers)
  3. guardian: any futures position with no protective stop gets one; a widened or cancelled stop
     is restored and logged as an override
  4. at each completed bar: 20-bar breakout check on MGC and MNQ -> Telegram alert with the
     ready-made bracket (you decide whether to take it); every signal is logged to signals.csv
  5. Friday 20:30 UTC: cancel the stops, close everything
  6. journal every trade and event to data/discipline/, recompute the bias metrics, git push

Usage (Windows, from the repo folder):
  python -m discipline_bot.bot run                      # the loop (Task Scheduler starts this)
  python -m discipline_bot.bot enter MNQ BUY 1 "note"   # queue an entry from the Lenovo itself
  python -m discipline_bot.bot status                   # positions, stops, today's P&L
"""
import csv, datetime as dt, json, math, os, subprocess, sys, time, uuid
from pathlib import Path

from ib_async import IB, Future, LimitOrder, MarketOrder, Order

from discipline_bot import config as C

ROOT = Path(__file__).resolve().parent.parent
JDIR = ROOT / C.JOURNAL_DIR
STATE = JDIR / "state.json"
TRADES = JDIR / "trades.csv"
EVENTS = JDIR / "events.csv"
METRICS = JDIR / "metrics.json"
SIGNALS = JDIR / "signals.csv"
QUEUE = ROOT / C.COMMAND_FILE
BLACKOUT = ROOT / C.BLACKOUT_FILE
TRADE_COLS = ["trade_id", "symbol", "side", "qty", "source", "entry_time", "entry_price", "atr",
              "initial_stop", "trail_dist", "risk_usd", "exit_time", "exit_price", "exit_reason",
              "pnl_usd", "r_multiple", "hours_held", "note"]

sys.path.insert(0, str(ROOT))
TG_FILE = Path(__file__).resolve().parent / "telegram.json"


COLOR = {"MGC": "🟡", "MNQ": "🔵", "MYM": "🟢", "M2K": "🟣", "MCL": "🟤", "ZN": "⚪", "MBT": "🟠"}
NAME = {"MGC": "Gold", "MNQ": "Nasdaq", "MYM": "Dow", "M2K": "Russell", "MCL": "Crude", "ZN": "10-yr note", "MBT": "Bitcoin"}
EXPECT_R = {"MGC": 0.22, "MNQ": 0.30, "MYM": 0.17}   # 2-year backtest of the squeeze rule, per trade after costs


def colorize(msg):
    """Put each market's colour dot in front of its symbol (MGC, MGCZ6 ...) so markets are easy to tell apart."""
    import re
    return re.sub(r"(?<![🟡🔵🟢🟣🟤⚪🟠\w])(MGC|MNQ|MYM|M2K|MCL|ZN|MBT)(?=\w*)", lambda m: COLOR[m.group(1)] + m.group(1), msg)


def send_telegram(msg, html=False):
    """Send through Discipline Bot's own Telegram bot (telegram.json); fall back to the repo's alerts helper."""
    msg = colorize(msg)
    try:
        if TG_FILE.exists():
            import requests
            cfg = json.loads(TG_FILE.read_text())
            data = {"chat_id": cfg["chat_id"], "text": msg}
            if html:
                data["parse_mode"] = "HTML"
            r = requests.post(f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage", data=data, timeout=15)
            if r.ok:
                return
            print("telegram error:", r.status_code, r.text[:200])
        from alerts import send_telegram as legacy
        legacy(msg)
    except Exception as e:
        print("[ALERT not sent]", e, msg)


SGT = dt.timezone(dt.timedelta(hours=8), "SGT")


def now():
    return dt.datetime.now(dt.timezone.utc)


def sgt(t):
    return t.astimezone(SGT).strftime("%a %d %b %H:%M SGT")


# ── journal ───────────────────────────────────────────────────────────────────
def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"open": {}, "processed_commands": [], "stops": {}}


def save_state(s):
    JDIR.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2, default=str))


def log_event(kind, symbol="", detail=""):
    JDIR.mkdir(parents=True, exist_ok=True)
    new = not EVENTS.exists()
    with EVENTS.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time_utc", "type", "symbol", "detail"])
        w.writerow([now().isoformat(timespec="seconds"), kind, symbol, detail])
    print(f"{now():%H:%M:%S} {kind} {symbol} {detail}")


def write_trade(row):
    JDIR.mkdir(parents=True, exist_ok=True)
    new = not TRADES.exists()
    with TRADES.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TRADE_COLS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in TRADE_COLS})


def update_metrics():
    """Bias mirror: the numbers that show whether loss aversion / disposition are creeping back."""
    if not TRADES.exists():
        return
    rows = list(csv.DictReader(TRADES.open()))
    closed = [r for r in rows if r["r_multiple"] not in ("", None, "nan")]
    ev = list(csv.DictReader(EVENTS.open())) if EVENTS.exists() else []
    def mean(xs): return round(sum(xs) / len(xs), 3) if xs else None
    wins = [r for r in closed if float(r["r_multiple"]) > 0]
    losses = [r for r in closed if float(r["r_multiple"]) <= 0]
    m = {
        "updated_utc": now().isoformat(timespec="seconds"),
        "closed_trades": len(closed),
        "win_rate": round(len(wins) / len(closed), 3) if closed else None,
        "avg_win_R": mean([float(r["r_multiple"]) for r in wins]),
        "avg_loss_R": mean([float(r["r_multiple"]) for r in losses]),
        "expectancy_R": mean([float(r["r_multiple"]) for r in closed]),
        # disposition effect: winners held shorter than losers, or winners closed by hand
        "avg_hours_winners": mean([float(r["hours_held"]) for r in wins]),
        "avg_hours_losers": mean([float(r["hours_held"]) for r in losses]),
        "winners_closed_by_hand": sum(1 for r in wins if r["exit_reason"] == "manual_exit"),
        # loss aversion: losses bigger than the planned 1R, stops widened or removed
        "losses_beyond_planned_stop": sum(1 for r in losses if float(r["r_multiple"]) < -1.15),
        "stop_overrides": sum(1 for e in ev if e["type"] == "override_restored"),
        "blocked_entries": sum(1 for e in ev if e["type"] == "blocked"),
        "note": "R = P&L / planned risk (1.5 x ATR). The backtest's Rule D averaged ~-1.27R per loss and won 37-42% of trades.",
    }
    METRICS.write_text(json.dumps(m, indent=2))


def git(*args):
    if not C.GIT_SYNC:
        return
    try:
        r = subprocess.run(["git", "-C", str(ROOT), *args], check=False, capture_output=True, text=True, timeout=60)
        if r.returncode and args[0] in ("push", "pull") and r.stderr.strip():
            print(f"git {args[0]} failed:", r.stderr.strip()[:300])
    except Exception as e:
        print("git error:", e)


def git_out(*args):
    try:
        return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=60).stdout.strip()
    except Exception:
        return ""


def git_push_journal():
    git("add", C.JOURNAL_DIR, C.COMMAND_FILE, *(["data/history"] if (ROOT / "data" / "history").exists() else []))
    git("commit", "-q", "-m", "discipline_bot journal update")
    git("push", "-q")


# ── market data / contracts ───────────────────────────────────────────────────
def front_contract(ib, sym):
    spec = C.INSTRUMENTS[sym]
    details = ib.reqContractDetails(Future(symbol=sym, exchange=spec["exchange"]))
    cutoff = (now() + dt.timedelta(days=spec["roll_days"])).strftime("%Y%m%d")
    live = sorted((d.contract for d in details if d.contract.lastTradeDateOrContractMonth[:8] > cutoff),
                  key=lambda c: c.lastTradeDateOrContractMonth)
    if not live:
        raise RuntimeError(f"no tradable {sym} contract found")
    c = live[0]
    d = next(x for x in details if x.contract.conId == c.conId)
    try:                                         # use IBKR's own contract specs
        spec["multiplier"] = float(c.multiplier or spec["multiplier"])
        spec["tick"] = float(d.minTick or spec["tick"])
    except (TypeError, ValueError):
        pass
    return c


def atr_and_last(ib, contract):
    bars = ib.reqHistoricalData(contract, endDateTime="", durationStr="5 D", barSizeSetting=C.BAR_SIZE,
                                whatToShow="TRADES", useRTH=False, formatDate=2)
    if len(bars) < C.ATR_PERIOD + 2:
        raise RuntimeError("not enough 4-hour bars for ATR")
    trs = []
    for prev, b in zip(bars[:-1], bars[1:]):
        trs.append(max(b.high - b.low, abs(b.high - prev.close), abs(b.low - prev.close)))
    atr = sum(trs[-C.ATR_PERIOD:]) / C.ATR_PERIOD
    last = ib.reqHistoricalData(contract, endDateTime="", durationStr="1800 S", barSizeSetting="1 min",
                                whatToShow="TRADES", useRTH=False, formatDate=2)
    price = last[-1].close if last else bars[-1].close
    return atr, price


def round_tick(x, tick):
    return round(round(x / tick) * tick, 10)


# ── guardrails ────────────────────────────────────────────────────────────────
def todays_realised_pnl(ib):
    """Realised P&L for today (Singapore date) from the trade journal, so it survives restarts
    (IBKR's fills list only covers the current connection)."""
    if not TRADES.exists():
        return 0.0
    today = now().astimezone(SGT).date()
    total = 0.0
    for r in csv.DictReader(TRADES.open()):
        try:
            if r["exit_time"] and dt.datetime.fromisoformat(r["exit_time"]).astimezone(SGT).date() == today:
                v = float(r["pnl_usd"])
                if not math.isnan(v):
                    total += v
        except (ValueError, KeyError):
            pass
    return total


CALENDAR = []          # high-impact USD events, refreshed from the weekly economic calendar


def refresh_calendar(state):
    """High-impact US releases (CPI, NFP, FOMC, PCE...) from the public weekly calendar feed. Feeds the
    entry blackout and the heads-up alerts. Refreshed every 3 hours."""
    global CALENDAR
    last = state.get("calendar_fetched")
    if last and (now() - dt.datetime.fromisoformat(last)).total_seconds() < 3 * 3600 and CALENDAR:
        return
    try:
        import requests
        data = requests.get(C.CALENDAR_URL, timeout=20, headers={"User-Agent": "discipline-bot"}).json()
        CALENDAR = sorted(({"time_utc": dt.datetime.fromisoformat(e["date"]).astimezone(dt.timezone.utc).isoformat(),
                            "name": e["title"], "forecast": e.get("forecast", ""), "previous": e.get("previous", "")}
                           for e in data if e.get("country") == "USD" and e.get("impact") == "High"),
                          key=lambda e: e["time_utc"])
        state["calendar_fetched"] = now().isoformat(timespec="seconds")
        state["calendar"] = CALENDAR
        log_event("calendar", "", f"{len(CALENDAR)} high-impact USD events this week")
    except Exception as e:
        CALENDAR = state.get("calendar", [])
        state["calendar_fetched"] = now().isoformat(timespec="seconds")   # retry in 3 h, don't hammer
        log_event("calendar_error", "", repr(e)[:200])


def calendar_alerts(state):
    """One heads-up per event, about 60 minutes before, with the Singapore time."""
    warned = state.setdefault("calendar_warned", [])
    for ev in CALENDAR:
        t = dt.datetime.fromisoformat(ev["time_utc"])
        mins = (t - now()).total_seconds() / 60
        key = ev["time_utc"] + ev["name"]
        if 0 < mins <= 60 and key not in warned:
            warned.append(key)
            extra = f" (forecast {ev['forecast']}, previous {ev['previous']})" if ev.get("forecast") else ""
            send_telegram(f"📅 High-impact US data in {mins:.0f} min: {ev['name']}{extra} at {sgt(t)}.\n"
                          f"No new entries {C.BLACKOUT_MINUTES} min either side; open stops stay in place.")
    state["calendar_warned"] = warned[-50:]


def calendar_text():
    up = [e for e in CALENDAR if dt.datetime.fromisoformat(e["time_utc"]) > now() - dt.timedelta(hours=1)]
    if not up:
        return "📅 No high-impact US releases left this week."
    return "📅 High-impact US releases (SGT):\n" + "\n".join(
        f"• {sgt(dt.datetime.fromisoformat(e['time_utc']))} — {e['name']}" for e in up[:15])


def in_blackout():
    for ev in CALENDAR:
        t = dt.datetime.fromisoformat(ev["time_utc"])
        if abs((now() - t).total_seconds()) <= C.BLACKOUT_MINUTES * 60:
            return ev["name"]
    if not BLACKOUT.exists():
        return None
    for ev in json.loads(BLACKOUT.read_text()).get("events", []):
        t = dt.datetime.fromisoformat(ev["time_utc"].replace("Z", "+00:00"))
        if abs((now() - t).total_seconds()) <= C.BLACKOUT_MINUTES * 60:
            return ev["name"]
    return None


def weekend_flatten_time():
    wd, hh, mm = C.WEEKEND_FLATTEN_UTC
    n = now()
    return n.replace(hour=hh, minute=mm, second=0, microsecond=0) + dt.timedelta(days=(wd - n.weekday()) % 7)


def check_entry(ib, state, sym, qty, atr):
    """Return None if the entry may go ahead, else the reason it is blocked."""
    spec = C.INSTRUMENTS.get(sym)
    if spec is None:
        return f"{sym} is not on the instrument list"
    held = {p.contract.symbol: p.position for p in ib.positions() if p.position}
    if sym in held:
        return f"already holding {sym}"
    if len(held) >= C.MAX_OPEN_POSITIONS:
        return f"already {len(held)} open positions (max {C.MAX_OPEN_POSITIONS})"
    if spec.get("index"):
        n_idx = sum(1 for s_ in held if C.INSTRUMENTS.get(s_, {}).get("index"))
        if n_idx >= C.MAX_INDEX_POSITIONS:
            return f"already {n_idx} stock-index positions (max {C.MAX_INDEX_POSITIONS}; they move together)"
    if qty < 1 or qty > C.MAX_CONTRACTS_PER_SYMBOL:
        return f"quantity {qty} outside 1..{C.MAX_CONTRACTS_PER_SYMBOL}"
    risk = C.INITIAL_STOP_ATR * atr * spec["multiplier"] * qty
    if risk > C.MAX_RISK_PER_TRADE_USD:
        return f"risk ${risk:,.0f} at 1.5xATR exceeds ${C.MAX_RISK_PER_TRADE_USD:,} — the stop is not shrunk to fit; trade smaller or wait"
    pnl = todays_realised_pnl(ib)
    if pnl <= -C.DAILY_LOSS_LIMIT_USD:
        return f"daily loss limit hit (${pnl:,.0f} today)"
    ev = in_blackout()
    if ev:
        return f"blackout around {ev}"
    if 0 <= (weekend_flatten_time() - now()).total_seconds() <= C.NO_ENTRY_HOURS_BEFORE_WEEKEND * 3600:
        return "too close to the weekend flatten"
    return None


# ── orders ────────────────────────────────────────────────────────────────────
def trail_order(action, qty, stop_price, trail_dist, parent_id=0):
    o = Order(action=action, totalQuantity=qty, orderType="TRAIL", auxPrice=trail_dist,
              trailStopPrice=stop_price, tif="GTC", outsideRth=True, orderRef=C.ORDER_REF)
    if parent_id:
        o.parentId = parent_id
    return o


def enter(ib, state, cmd):
    sym, side, qty = cmd["symbol"].upper(), cmd["side"].upper(), int(cmd.get("qty", 1))
    spec = C.INSTRUMENTS[sym]
    if sym in state["open"] and not any(p.position for p in ib.positions() if p.contract.symbol == sym):
        record_exits(ib, state, force=(sym,))   # previous trade in this market closed since the last loop: journal it first
    contract = front_contract(ib, sym)
    atr, price = atr_and_last(ib, contract)
    why = check_entry(ib, state, sym, qty, atr)
    if why:
        log_event("blocked", sym, why)
        send_telegram(f"🛑 discipline_bot blocked {side} {qty} {sym}: {why}")
        return
    sign = 1 if side == "BUY" else -1
    stop = round_tick(price - sign * C.INITIAL_STOP_ATR * atr, spec["tick"])
    trail = round_tick(C.TRAIL_ATR * atr, spec["tick"])
    # IBKR only attaches a trailing stop to a LIMIT parent (error 328), so enter with a marketable limit:
    # a few ticks through the market, which fills like a market order but caps slippage.
    buffer = max(8 * spec["tick"], round_tick(0.1 * atr, spec["tick"]))
    lmt = round_tick(price + sign * buffer, spec["tick"])
    parent = LimitOrder(side, qty, lmt, orderRef=C.ORDER_REF, outsideRth=True, tif="DAY")
    parent.orderId = ib.client.getReqId()
    parent.transmit = False
    child = trail_order("SELL" if side == "BUY" else "BUY", qty, stop, trail, parent.orderId)
    child.transmit = True                      # transmits parent + child together: no moment without a stop
    t_parent = ib.placeOrder(contract, parent)
    t_child = ib.placeOrder(contract, child)
    for _ in range(20):                         # wait up to ~20 s for the fill
        ib.sleep(1)
        if t_parent.orderStatus.status in ("Filled", "Cancelled", "ApiCancelled", "Inactive"):
            break
    filled = t_parent.orderStatus.status == "Filled"
    if not filled:
        why = "; ".join(e.message for e in t_parent.log if e.message)[-250:]
        if t_parent.orderStatus.status not in ("Cancelled", "ApiCancelled", "Inactive"):
            ib.cancelOrder(parent)              # don't leave an unfilled entry working
        log_event("entry_failed", sym, f"{side} {qty} limit {lmt}: status {t_parent.orderStatus.status}; {why or 'no message'}")
        send_telegram(f"⚠️ {sym} {side} entry NOT filled (limit {lmt}, status {t_parent.orderStatus.status}); cancelled. {why}"[:400])
        return
    fill = t_parent.orderStatus.avgFillPrice
    tid = uuid.uuid4().hex[:8]
    risk = C.INITIAL_STOP_ATR * atr * spec["multiplier"] * qty
    state["open"][sym] = dict(trade_id=tid, symbol=sym, side=side, qty=qty, source="bot",
                              entry_time=now().isoformat(timespec="seconds"), entry_price=fill,
                              atr=round(atr, 4), initial_stop=stop, trail_dist=trail, risk_usd=round(risk, 2),
                              note=cmd.get("note", ""))
    state["stops"][sym] = {"orderId": t_child.order.orderId, "trail": trail}
    log_event("entry", sym, f"{side} {qty} @ {fill} | stop {stop} | trail {trail} | risk ${risk:,.0f}")
    send_telegram(f"✅ {side} {qty} {sym} @ {fill}\nStop {stop} (1.5×ATR), trails {trail} (3×ATR). Risk ${risk:,.0f}.")


STOP_TYPES = ("TRAIL", "STP", "STP LMT", "TRAIL LIMIT")


def protective_stop(ib, contract, trades=None):
    """Find a working stop on this contract placed by ANY client (TWS, mobile, web, this bot).
    ib.openTrades() only shows this client's own orders, so ask IBKR for all of them."""
    for t in (trades if trades is not None else ib.reqAllOpenOrders()):
        if t.contract.conId == contract.conId and t.order.orderType in STOP_TYPES:
            return t
    return None


def guardian(ib, state):
    """Every position must have a stop; a stop may never be widened or removed."""
    all_trades = ib.reqAllOpenOrders()
    for p in ib.positions():
        if not p.position or p.contract.secType != "FUT" or p.contract.symbol not in C.INSTRUMENTS:
            continue
        sym, spec = p.contract.symbol, C.INSTRUMENTS[p.contract.symbol]
        if sym in state.get("pending_reason", {}):      # the bot is closing it right now
            continue
        contract = ib.qualifyContracts(p.contract)[0] if not p.contract.exchange else p.contract
        if not contract.exchange:
            contract.exchange = spec["exchange"]
        t = protective_stop(ib, contract, all_trades)
        known = state["stops"].get(sym)
        if t and known and t.order.auxPrice and t.order.auxPrice > known["trail"] * 1.001:
            t.order.auxPrice = known["trail"]
            ib.placeOrder(contract, t.order)
            log_event("override_restored", sym, f"trail widened to {t.order.auxPrice}; restored to {known['trail']}")
            send_telegram(f"⚠️ {sym} stop was widened by hand — restored. Widening a stop is loss aversion talking.")
            continue
        if t:
            if sym not in state["open"]:          # e.g. lost after a restart: start journaling it again
                state["open"][sym] = dict(trade_id=uuid.uuid4().hex[:8], symbol=sym, side="BUY" if p.position > 0 else "SELL",
                                          qty=abs(p.position), source="adopted", entry_time=now().isoformat(timespec="seconds"),
                                          entry_price=round(p.avgCost / spec["multiplier"], 2), atr="", initial_stop=t.order.trailStopPrice,
                                          trail_dist=t.order.auxPrice,
                                          risk_usd=round(abs(p.avgCost / spec["multiplier"] - (t.order.trailStopPrice or 0)) * spec["multiplier"] * abs(p.position), 2) or 1)
                state["stops"].setdefault(sym, {"orderId": t.order.orderId, "trail": t.order.auxPrice})
                log_event("adopted", sym, f"{p.position} @ {state['open'][sym]['entry_price']} with existing {t.order.orderType} stop")
            continue
        atr, price = atr_and_last(ib, contract)
        entry = p.avgCost / spec["multiplier"]
        side = "BUY" if p.position > 0 else "SELL"
        sign = 1 if p.position > 0 else -1
        stop = round_tick(entry - sign * C.INITIAL_STOP_ATR * atr, spec["tick"])
        if (sign > 0 and stop >= price) or (sign < 0 and stop <= price):   # already beyond the stop
            stop = round_tick(price - sign * spec["tick"] * 4, spec["tick"])
        trail = round_tick(C.TRAIL_ATR * atr, spec["tick"])
        o = trail_order("SELL" if sign > 0 else "BUY", abs(p.position), stop, trail)
        tr = ib.placeOrder(contract, o)
        reason = "override_restored" if known else "guarded_manual"
        state["stops"][sym] = {"orderId": tr.order.orderId, "trail": trail}
        if sym not in state["open"]:
            state["open"][sym] = dict(trade_id=uuid.uuid4().hex[:8], symbol=sym, side=side, qty=abs(p.position),
                                      source="manual-guarded", entry_time=now().isoformat(timespec="seconds"),
                                      entry_price=entry, atr=round(atr, 4), initial_stop=stop, trail_dist=trail,
                                      risk_usd=round(C.INITIAL_STOP_ATR * atr * spec["multiplier"] * abs(p.position), 2))
        log_event(reason, sym, f"stop attached at {stop}, trail {trail}")
        send_telegram(f"🛡️ {sym} position had no stop — attached {stop}, trailing {trail}.")


def flatten(ib, state, sym, reason):
    """Close a position the bot protects. A position whose stop was placed elsewhere (TWS, mobile) is NOT
    market-closed: this client can't cancel that stop, and closing would leave it live to open a new
    position later. You get an alert to close it yourself instead."""
    if sym not in state["stops"]:
        held = [p for p in ib.positions() if p.contract.symbol == sym and p.position]
        if held:
            log_event("flatten_skipped", sym, f"{reason}: stop not placed by the bot")
            send_telegram(f"⚠️ {reason}: please close your {sym} position and its stop yourself — "
                          f"the stop was placed outside the bot, so the bot can't safely cancel it.")
        return
    for t in ib.openTrades():
        if t.contract.symbol == sym and t.order.orderType in STOP_TYPES:
            ib.cancelOrder(t.order)
    ib.sleep(2)
    for p in ib.positions():
        if p.contract.symbol == sym and p.position:
            c = p.contract
            if not c.exchange:
                c.exchange = C.INSTRUMENTS[sym]["exchange"]
            ib.placeOrder(c, MarketOrder("SELL" if p.position > 0 else "BUY", abs(p.position),
                                         orderRef=C.ORDER_REF, outsideRth=True))
    state.setdefault("pending_reason", {})[sym] = reason
    log_event("flatten", sym, reason)


def record_exits(ib, state, force=()):
    held = {p.contract.symbol for p in ib.positions() if p.position}
    missing = state.setdefault("missing_since", {})
    for sym in list(state["open"]):
        if sym in held:
            missing.pop(sym, None)
            continue
        tr = state["open"][sym]
        age = (now() - dt.datetime.fromisoformat(tr["entry_time"])).total_seconds()
        if sym not in force:
            if age < 180:                      # IBKR can take a moment to report a new position
                continue
            missing.setdefault(sym, now().isoformat(timespec="seconds"))
            if (now() - dt.datetime.fromisoformat(missing[sym])).total_seconds() < 120:
                continue                            # must be gone for 2 minutes before we call it closed
        entry_t = dt.datetime.fromisoformat(tr["entry_time"])
        spec = C.INSTRUMENTS[sym]
        fills = sorted((f for f in ib.fills() if f.contract.symbol == sym and f.time >= entry_t), key=lambda f: f.time)
        closing = [f for f in fills if f.execution.side == ("SLD" if tr["side"] == "BUY" else "BOT")]
        if not closing:
            log_event("position_missing", sym, "no position and no closing fill found; dropped from journal (entry may not have filled)")
            state["open"].pop(sym); state["stops"].pop(sym, None); missing.pop(sym, None)
            continue
        state["open"].pop(sym); state["stops"].pop(sym, None); missing.pop(sym, None)
        last = closing[-1]
        exit_price = last.execution.avgPrice
        reason = state.get("pending_reason", {}).pop(sym, None)
        if reason is None:
            reason = "trailing_stop" if last and last.execution.side in ("SLD", "BOT") and \
                any(t.order.orderType.startswith("TRAIL") for t in ib.trades()
                    if t.order.orderId == last.execution.orderId) else "other"
        sign = 1 if tr["side"] == "BUY" else -1
        pnl = sign * (exit_price - float(tr["entry_price"])) * spec["multiplier"] * int(tr["qty"])
        hours = (now() - dt.datetime.fromisoformat(tr["entry_time"])).total_seconds() / 3600
        tr.update(exit_time=now().isoformat(timespec="seconds"), exit_price=exit_price, exit_reason=reason,
                  pnl_usd=round(pnl, 2), r_multiple=round(pnl / float(tr["risk_usd"]), 3), hours_held=round(hours, 1))
        write_trade(tr)
        log_event("exit", sym, f"{reason} @ {exit_price} | P&L ${pnl:,.0f} | {tr['r_multiple']}R | {hours:.1f}h")
        send_telegram(f"🏁 {sym} closed ({reason}) @ {exit_price}: ${pnl:,.0f}, {tr['r_multiple']}R after {hours:.1f}h")


# ── entry signals (alerts only) ───────────────────────────────────────────────
BARS = {}   # localSymbol -> list of completed+forming 15m bars, kept between loops


def history(ib, contract):
    """~30 days of 15m bars: fetched in 10-day chunks once, then topped up with the last day each bar."""
    key = contract.localSymbol
    def req(end, dur):
        return ib.reqHistoricalData(contract, endDateTime=end, durationStr=dur, barSizeSetting=C.BAR_SIZE,
                                    whatToShow="TRADES", useRTH=False, formatDate=2)
    if key not in BARS:
        bars, end = [], ""
        for _ in range(C.HISTORY_DAYS // 10):
            chunk = req(end, "10 D")
            if not chunk:
                break
            bars = list(chunk) + [b for b in bars if b.date > chunk[-1].date]
            first = chunk[0].date
            end = (first - dt.timedelta(seconds=1)).astimezone(dt.timezone.utc).strftime("%Y%m%d-%H:%M:%S")
        BARS[key] = bars
    else:
        top = req("", "1 D")
        if top:
            BARS[key] = ([b for b in BARS[key] if b.date < top[0].date] + list(top))[-3000:]
    return BARS[key]


def check_signals(ib, state):
    """Squeeze-breakout-with-trend on the last COMPLETED bar (see signals.py), for markets with entries on."""
    seen = state.setdefault("signal_bar", {})
    for sym, spec in C.INSTRUMENTS.items():
        if not spec.get("entries", True):
            continue
        try:
            check_one_signal(ib, state, seen, sym, spec)
        except Exception as e:
            log_event("signal_error", sym, repr(e)[:200])


def check_one_signal(ib, state, seen, sym, spec):
    from discipline_bot.signals import squeeze_signal
    contract = front_contract(ib, sym)
    bars = history(ib, contract)
    done = bars[:-1]                       # the last bar returned is still forming
    if len(done) < C.ATR_PERIOD + 3:
        return
    bar = done[-1]
    key = str(bar.date)
    if seen.get(sym) == key:
        return
    seen[sym] = key
    sign, reason = squeeze_signal([b.high for b in done], [b.low for b in done], [b.close for b in done])
    state.setdefault("signal_status", {})[sym] = f"{now().astimezone(SGT):%H:%M} {reason}"
    if not sign:
        if reason.startswith("warming"):
            log_event("signal_info", sym, reason)
        return
    side = "BUY" if sign > 0 else "SELL"
    trs = [max(b.high - b.low, abs(b.high - p.close), abs(b.low - p.close)) for p, b in zip(done[:-1], done[1:])]
    atr = sum(trs[-C.ATR_PERIOD:]) / C.ATR_PERIOD
    stop = round_tick(bar.close - sign * C.INITIAL_STOP_ATR * atr, spec["tick"])
    trail = round_tick(C.TRAIL_ATR * atr, spec["tick"])
    risk = C.INITIAL_STOP_ATR * atr * spec["multiplier"]
    why = check_entry(ib, state, sym, 1, atr)
    JDIR.mkdir(parents=True, exist_ok=True)
    new = not SIGNALS.exists()
    with SIGNALS.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["bar_time", "symbol", "contract", "side", "close", "breakout_level", "atr", "stop", "trail",
                        "risk_usd", "guardrails", "taken"])
        w.writerow([key, sym, contract.localSymbol, side, bar.close, reason, round(atr, 4), stop, trail,
                    round(risk), why or "pass", ""])
    log_event("signal", sym, f"{side} {contract.localSymbol} close {bar.close}: {reason}")
    if C.AUTO_ENTRIES and not why:
        send_telegram(f"🤖 {sym} {reason} ({C.BAR_SIZE} close {bar.close}). Entering {side} 1 automatically (paper).")
        enter(ib, state, {"symbol": sym, "side": side, "qty": 1, "note": f"auto squeeze {key}"})
        return
    send_telegram(f"📈 {sym} {reason} ({C.BAR_SIZE} close {bar.close}).\n{side} setup on {contract.localSymbol}: "
                  f"stop {stop}, trail {trail}, risk ${risk:,.0f} per contract.\n"
                  f"Guardrails: {'PASS' if not why else 'BLOCKED — ' + why}")


def write_snapshot(ib, state):
    """Positions, prices, P&L and stops, written each bar so Claude can read them from GitHub."""
    trades = ib.reqAllOpenOrders()
    snap = {"time_utc": now().isoformat(timespec="seconds"), "bar_size": C.BAR_SIZE,
            "auto_entries": C.AUTO_ENTRIES, "signal_status": state.get("signal_status", {}), "positions": []}
    for it in ib.portfolio():
        if not it.position:
            continue
        mult = C.INSTRUMENTS.get(it.contract.symbol, {"multiplier": 1})["multiplier"]
        t = protective_stop(ib, it.contract, trades)
        snap["positions"].append({
            "contract": it.contract.localSymbol, "qty": it.position, "avg_price": round(it.averageCost / mult, 2),
            "last": it.marketPrice, "unrealized_usd": round(it.unrealizedPNL, 2),
            "stop": None if not t else {"type": t.order.orderType, "trail": t.order.auxPrice,
                                        "stop_price": t.order.trailStopPrice, "status": t.orderStatus.status}})
    (JDIR / "snapshot.json").write_text(json.dumps(snap, indent=2, default=str))


STRATEGY = ("<b>Strategy</b>: squeeze breakout with 1h trend, 15-min bars\n"
            "Entry = quiet market (40-bar band width in lowest 20%) + close beyond 40-bar high/low + 1h trend agrees\n"
            "Exit = stop 1.5 ATR, trail 3 ATR, no target · entries on 🟡MGC 🔵MNQ 🟢MYM")


def _esc(x):
    return str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def status_text(ib, state):
    write_snapshot(ib, state)
    snap = json.loads((JDIR / "snapshot.json").read_text())
    out = [f"📊 <b>Status {now().astimezone(SGT):%a %d %b %H:%M} SGT</b> · auto {'ON' if C.AUTO_ENTRIES else 'OFF'}", STRATEGY, ""]
    total = 0.0
    pos = sorted(snap["positions"], key=lambda p: list(COLOR).index(p["contract"][:3]) if p["contract"][:3] in COLOR else 9)
    out.append("<b>Open positions</b>")
    for p_ in pos:
        st, u = p_["stop"], p_["unrealized_usd"] or 0
        total += u
        sym = next((k for k in COLOR if p_["contract"].startswith(k)), p_["contract"])
        out.append(f"{'📈 LONG' if p_['qty'] > 0 else '📉 SHORT'} <b>{_esc(p_['contract'])}</b> ({NAME.get(sym, '')})\n"
                   f"   entry {p_['avg_price']} → now {p_['last']:.2f}\n"
                   f"   {'✅' if u >= 0 else '🔻'} <b>{'+' if u >= 0 else '−'}${abs(u):,.0f}</b> · "
                   + (f"stop {st['stop_price']} (trail {st['trail']})" if st else "⚠️ NO STOP"))
    if not pos:
        out.append("None.")
    out.append(f"\nOpen P&amp;L <b>${total:,.0f}</b> · realised today <b>${todays_realised_pnl(ib):,.0f}</b>")
    if state.get("signal_status"):
        out.append("\n<b>Setups</b> (needs squeeze + breakout + trend)")
        for k, v in state["signal_status"].items():
            out.append(f"{k}: {_esc(v)}")
    out.append("\n/summary for performance")
    return "\n".join(out)


def summary_text(ib):
    rows = list(csv.DictReader(TRADES.open())) if TRADES.exists() else []
    def num(x):
        try:
            v = float(x)
            return None if math.isnan(v) else v
        except (TypeError, ValueError):
            return None
    new = [r for r in rows if "squeeze" in (r.get("note") or "") and num(r.get("pnl_usd")) is not None]
    old = [r for r in rows if "squeeze" not in (r.get("note") or "") and num(r.get("pnl_usd")) is not None]
    out = [f"🏁 <b>Performance {now().astimezone(SGT):%a %d %b %H:%M} SGT</b>", STRATEGY, "",
           "<b>Squeeze rule</b> (live since 25 Nov 11:38 SGT), closed trades:"]
    for sym in EXPECT_R:
        t = [r for r in new if r["symbol"] == sym]
        if not t:
            out.append(f"{sym} ({NAME[sym]}): no closed trades yet · backtest +{EXPECT_R[sym]}R/trade")
            continue
        pnl = sum(num(r["pnl_usd"]) for r in t); rs = [num(r["r_multiple"]) or 0 for r in t]
        w = sum(1 for x in rs if x > 0)
        out.append(f"{sym} ({NAME[sym]}): {len(t)} trades · {w} won · <b>{'+' if pnl >= 0 else '−'}${abs(pnl):,.0f}</b> · "
                   f"{sum(rs)/len(rs):+.2f}R/trade (backtest +{EXPECT_R[sym]}R)")
    if new:
        pnl = sum(num(r["pnl_usd"]) for r in new); rs = [num(r["r_multiple"]) or 0 for r in new]
        out.append(f"<b>Total: {len(new)} trades · {sum(1 for x in rs if x > 0)} won · "
                   f"{'+' if pnl >= 0 else '−'}${abs(pnl):,.0f} · {sum(rs)/len(rs):+.2f}R/trade</b>")
        big = max(rs); out.append(f"Biggest win {big:+.1f}R · losers cut at about −1R as designed")
    out.append("Expect: about 1 win in 3; profit comes from a few big winners. Judge after 30+ trades, not days.")
    if old:
        pnl = sum(num(r["pnl_usd"]) for r in old)
        out.append(f"\n<i>Old 20-bar breakout rule (retired): {len(old)} trades · {'+' if pnl >= 0 else '−'}${abs(pnl):,.0f}</i>")
    out.append("\n/status for positions and setups")
    return "\n".join(out)



def telegram_commands(ib, state):
    """Answer /status (or 'status') typed in the Discipline Bot chat, so positions can be checked from the
    phone without logging in to IBKR (which would knock IB Gateway off the paper login)."""
    if not TG_FILE.exists():
        return
    import requests
    cfg = json.loads(TG_FILE.read_text())
    try:
        r = requests.get(f"https://api.telegram.org/bot{cfg['bot_token']}/getUpdates",
                         params={"offset": state.get("tg_offset", 0), "timeout": 0}, timeout=15).json()
    except Exception as e:
        log_event("telegram_error", "", f"getUpdates failed: {e!r}"[:200])
        return
    if not r.get("ok"):
        log_event("telegram_error", "", f"getUpdates: {str(r)[:200]}")
        return
    for u in r.get("result", []):
        state["tg_offset"] = u["update_id"] + 1
        msg = u.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != str(cfg["chat_id"]):
            log_event("telegram_cmd", "", f"ignored message from chat {msg.get('chat', {}).get('id')}")
            continue                                # only answer you
        text = (msg.get("text") or "").strip().lower()
        log_event("telegram_cmd", "", text[:40])
        if text in ("/status", "status", "/positions", "positions"):
            send_telegram(status_text(ib, state), html=True)
        elif text in ("/summary", "summary", "/performance", "performance"):
            send_telegram(summary_text(ib), html=True)
        elif text in ("/calendar", "calendar", "/macro", "macro"):
            send_telegram(calendar_text())
        elif text in ("/help", "help", "/start"):
            send_telegram("/status — strategy, positions, stops and setups\n/summary — performance so far vs the backtest\n"
                          "/calendar — this week's high-impact US releases (SGT)")


# ── commands ──────────────────────────────────────────────────────────────────
def read_queue():
    if not QUEUE.exists():
        return []
    return json.loads(QUEUE.read_text() or "[]")


def process_commands(ib, state):
    for cmd in read_queue():
        cid = cmd.get("id")
        if not cid or cid in state["processed_commands"]:
            continue
        state["processed_commands"].append(cid)
        try:
            if cmd["action"] == "enter":
                enter(ib, state, cmd)
            elif cmd["action"] == "exit":
                flatten(ib, state, cmd["symbol"].upper(), "manual_exit")
            elif cmd["action"] == "download_daily":
                root = Path(__file__).resolve().parent.parent
                (root / "data" / "history" / "daily").mkdir(parents=True, exist_ok=True)
                log = open(root / "data" / "history" / "daily" / "download.log", "a")
                subprocess.Popen([sys.executable, "-u", "-m", "discipline_bot.download_daily"], cwd=root,
                                 stdout=log, stderr=subprocess.STDOUT)
                log_event("download_started", "", "20-year daily ETF + VIX history download started")
            elif cmd["action"] == "download_history":
                root = Path(__file__).resolve().parent.parent
                (root / "data" / "history").mkdir(parents=True, exist_ok=True)
                log = open(root / "data" / "history" / "download.log", "a")
                subprocess.Popen([sys.executable, "-u", "-m", "discipline_bot.download"], cwd=root,
                                 stdout=log, stderr=subprocess.STDOUT)
                log_event("download_started", "", "2-year 15-minute history download started")
            else:
                log_event("blocked", cmd.get("symbol", ""), f"unknown action {cmd['action']}")
        except Exception as e:
            log_event("error", cmd.get("symbol", ""), f"{cid}: {e}")


# ── main loop ─────────────────────────────────────────────────────────────────
BENIGN = {1100, 1101, 1102, 2103, 2104, 2105, 2106, 2107, 2108, 2109, 2119, 2157, 2158, 10167}


def on_error(reqId, code, msg, contract):
    """Record IBKR errors (order rejections etc.) in the journal so they are visible remotely."""
    if code not in BENIGN:
        log_event("ibkr_error", getattr(contract, "symbol", "") if contract else "", f"{code} (req {reqId}): {msg}"[:300])


def connect():
    ib = IB()
    ib.errorEvent += on_error
    ib.connect(C.IB_HOST, C.IB_PORT, clientId=C.CLIENT_ID)
    accts = ib.managedAccounts()
    if not C.ALLOW_LIVE and not all(a.startswith("DU") for a in accts):
        ib.disconnect()
        raise SystemExit(f"Refusing to run: account(s) {accts} are not paper and ALLOW_LIVE is False.")
    return ib


def run():
    state = load_state()
    ib, flattened_for = None, None
    code_rev = git_out("rev-parse", "HEAD")
    log_event("start", "", f"port {C.IB_PORT} | code {code_rev[:7]} | auto entries {'ON' if C.AUTO_ENTRIES else 'OFF'}")
    send_telegram(f"✅ Discipline Bot online (paper). Bars: {C.BAR_SIZE}. Auto entries: {'ON' if C.AUTO_ENTRIES else 'OFF'}. "
                  f"I'll message you here about entries, exits, blocked trades and errors.")
    while True:
        try:
            if ib is None or not ib.isConnected():
                ib = connect()
                log_event("connected", "", ",".join(ib.managedAccounts()))
            git("pull", "-q", "--rebase", "--autostash")
            new_rev = git_out("rev-parse", "HEAD")
            if code_rev and new_rev != code_rev and git_out("diff", "--name-only", code_rev, new_rev, "--", "discipline_bot/*.py"):
                log_event("restart", "", f"code updated {code_rev[:7]} -> {new_rev[:7]}; restarting")
                save_state(state)
                git_push_journal()
                ib.disconnect()
                return                                  # run_bot.bat starts the new code within 5 seconds
            code_rev = new_rev or code_rev
            process_commands(ib, state)
            refresh_calendar(state)
            calendar_alerts(state)
            telegram_commands(ib, state)
            n = now()
            bar_key = n.strftime("%Y%m%d%H") + f"{n.minute // C.BAR_MINUTES:02d}"
            if C.SIGNAL_ALERTS and n.minute % C.BAR_MINUTES < 5 and state.get("signal_checked") != bar_key:
                state["signal_checked"] = bar_key
                check_signals(ib, state)
                write_snapshot(ib, state)
            if C.GUARD_MANUAL_POSITIONS or state["open"]:
                guardian(ib, state)
            fl = weekend_flatten_time()
            if now() >= fl and (now() - fl).total_seconds() < 3600 and flattened_for != fl.date():
                for sym in {p.contract.symbol for p in ib.positions() if p.position}:
                    flatten(ib, state, sym, "weekend_flat")
                flattened_for = fl.date()
            ib.sleep(2)
            record_exits(ib, state)
            save_state(state)
            update_metrics()
            git_push_journal()
        except SystemExit:
            raise
        except Exception as e:
            log_event("error", "", repr(e))
            last_alert = state.get("last_error_alert")
            if not last_alert or (now() - dt.datetime.fromisoformat(last_alert)).total_seconds() > 1800:
                state["last_error_alert"] = now().isoformat(timespec="seconds")   # at most one alert per 30 min
                send_telegram(f"⚠️ discipline_bot error (is IB Gateway logged in?): {e!r}"[:300])
            try:
                if ib:
                    ib.disconnect()
            except Exception:
                pass
            ib = None
            try:                                   # still publish the journal so the error is visible remotely
                save_state(state)
                git_push_journal()
            except Exception:
                pass
        time.sleep(C.LOOP_SECONDS)


def queue_entry(sym, side, qty, note=""):
    q = read_queue()
    q.append({"id": uuid.uuid4().hex[:10], "action": "enter", "symbol": sym.upper(), "side": side.upper(),
              "qty": int(qty), "note": note, "created_utc": now().isoformat(timespec="seconds")})
    QUEUE.write_text(json.dumps(q, indent=2))
    print("queued:", q[-1])


def status():
    ib = connect()
    for p in ib.positions():
        if p.position:
            t = protective_stop(ib, p.contract)
            print(f"{p.contract.localSymbol}: {p.position} @ {p.avgCost / C.INSTRUMENTS.get(p.contract.symbol, {'multiplier': 1})['multiplier']:.2f}"
                  f" | stop: {('TRAIL ' + str(t.order.auxPrice)) if t else 'NONE'}")
    print(f"realised today: ${todays_realised_pnl(ib):,.0f}")
    ib.disconnect()


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] == "run":
        run()
    elif a[0] == "enter" and len(a) >= 4:
        queue_entry(a[1], a[2], a[3], " ".join(a[4:]))
    elif a[0] == "exit" and len(a) >= 2:
        q = read_queue(); q.append({"id": uuid.uuid4().hex[:10], "action": "exit", "symbol": a[1].upper()})
        QUEUE.write_text(json.dumps(q, indent=2)); print("queued exit", a[1])
    elif a[0] == "status":
        status()
    else:
        print(__doc__)
