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

from ib_async import IB, Future, MarketOrder, Order

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


def send_telegram(msg):
    """Send through Discipline Bot's own Telegram bot (telegram.json); fall back to the repo's alerts helper."""
    try:
        if TG_FILE.exists():
            import requests
            cfg = json.loads(TG_FILE.read_text())
            r = requests.post(f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage",
                              data={"chat_id": cfg["chat_id"], "text": msg}, timeout=15)
            if r.ok:
                return
            print("telegram error:", r.status_code, r.text[:200])
        from alerts import send_telegram as legacy
        legacy(msg)
    except Exception as e:
        print("[ALERT not sent]", e, msg)


def now():
    return dt.datetime.now(dt.timezone.utc)


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
    closed = [r for r in rows if r["r_multiple"] not in ("", None)]
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
    git("add", C.JOURNAL_DIR, C.COMMAND_FILE)
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
    return live[0]


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
    today = now().date()
    return sum(f.commissionReport.realizedPNL or 0 for f in ib.fills()
               if f.time.date() == today and f.commissionReport.realizedPNL
               and not math.isnan(f.commissionReport.realizedPNL))


def in_blackout():
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
    parent = MarketOrder(side, qty, orderRef=C.ORDER_REF, outsideRth=True)
    parent.orderId = ib.client.getReqId()
    parent.transmit = False
    child = trail_order("SELL" if side == "BUY" else "BUY", qty, stop, trail, parent.orderId)
    child.transmit = True                      # transmits parent + child together: no moment without a stop
    ib.placeOrder(contract, parent)
    t_child = ib.placeOrder(contract, child)
    ib.sleep(3)
    fill = next((f.execution.avgPrice for f in ib.fills() if f.execution.orderId == parent.orderId), price)
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


def record_exits(ib, state):
    held = {p.contract.symbol for p in ib.positions() if p.position}
    for sym in list(state["open"]):
        if sym in held:
            continue
        tr = state["open"].pop(sym)
        state["stops"].pop(sym, None)
        spec = C.INSTRUMENTS[sym]
        fills = sorted((f for f in ib.fills() if f.contract.symbol == sym), key=lambda f: f.time)
        last = fills[-1] if fills else None
        exit_price = last.execution.avgPrice if last else float("nan")
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
def check_signals(ib, state):
    """20-bar breakout on the last COMPLETED bar. Sends an alert; never places an order."""
    seen = state.setdefault("signal_bar", {})
    for sym, spec in C.INSTRUMENTS.items():
        contract = front_contract(ib, sym)
        bars = ib.reqHistoricalData(contract, endDateTime="", durationStr="5 D", barSizeSetting=C.BAR_SIZE,
                                    whatToShow="TRADES", useRTH=False, formatDate=2)
        if len(bars) < max(C.BREAKOUT_BARS, C.ATR_PERIOD) + 3:
            continue
        done = bars[:-1]                       # the last bar returned is still forming
        bar, prior = done[-1], done[-1 - C.BREAKOUT_BARS:-1]
        key = str(bar.date)
        if seen.get(sym) == key:
            continue
        seen[sym] = key
        hi, lo = max(b.high for b in prior), min(b.low for b in prior)
        side = "BUY" if bar.close > hi else "SELL" if bar.close < lo else None
        if not side:
            continue
        trs = [max(b.high - b.low, abs(b.high - p.close), abs(b.low - p.close)) for p, b in zip(done[:-1], done[1:])]
        atr = sum(trs[-C.ATR_PERIOD:]) / C.ATR_PERIOD
        sign = 1 if side == "BUY" else -1
        stop = round_tick(bar.close - sign * C.INITIAL_STOP_ATR * atr, spec["tick"])
        trail = round_tick(C.TRAIL_ATR * atr, spec["tick"])
        risk = C.INITIAL_STOP_ATR * atr * spec["multiplier"]
        why = check_entry(ib, state, sym, 1, atr)
        level = hi if side == "BUY" else lo
        JDIR.mkdir(parents=True, exist_ok=True)
        new = not SIGNALS.exists()
        with SIGNALS.open("a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["bar_time", "symbol", "contract", "side", "close", "breakout_level", "atr", "stop", "trail",
                            "risk_usd", "guardrails", "taken"])
            w.writerow([key, sym, contract.localSymbol, side, bar.close, level, round(atr, 4), stop, trail,
                        round(risk), why or "pass", ""])
        log_event("signal", sym, f"{side} {contract.localSymbol} close {bar.close} vs 20-bar {'high' if sign > 0 else 'low'} {level}")
        verdict = f"Guardrails: PASS" if not why else f"Guardrails: BLOCKED — {why}"
        if C.AUTO_ENTRIES and not why:
            send_telegram(f"🤖 {sym} breakout: {C.BAR_SIZE} bar closed {bar.close} {'above' if sign > 0 else 'below'} the 20-bar "
                          f"{'high' if sign > 0 else 'low'} {level}. Entering {side} 1 automatically (paper).")
            enter(ib, state, {"symbol": sym, "side": side, "qty": 1, "note": f"auto breakout {key}"})
            continue
        send_telegram(f"📈 {sym} breakout: {C.BAR_SIZE} bar closed {bar.close} {'above' if sign > 0 else 'below'} the 20-bar "
                      f"{'high' if sign > 0 else 'low'} {level}.\n{side} setup on {contract.localSymbol}: stop {stop}, "
                      f"trail {trail}, risk ${risk:,.0f} per contract.\n{verdict}\nTell Claude \"take it\" or ignore.")


def write_snapshot(ib, state):
    """Positions, prices, P&L and stops, written each bar so Claude can read them from GitHub."""
    trades = ib.reqAllOpenOrders()
    snap = {"time_utc": now().isoformat(timespec="seconds"), "bar_size": C.BAR_SIZE,
            "auto_entries": C.AUTO_ENTRIES, "positions": []}
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
            else:
                log_event("blocked", cmd.get("symbol", ""), f"unknown action {cmd['action']}")
        except Exception as e:
            log_event("error", cmd.get("symbol", ""), f"{cid}: {e}")


# ── main loop ─────────────────────────────────────────────────────────────────
def connect():
    ib = IB()
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
            send_telegram(f"⚠️ discipline_bot error: {e!r}"[:300])
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
