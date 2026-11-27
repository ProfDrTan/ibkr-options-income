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

from ib_async import IB, Future, Index, LimitOrder, MarketOrder, Order

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
EXPECT_R = {"MGC": 0.17, "MNQ": 0.21, "MYM": 0.12}   # 2-year backtest, squeeze + 24h breakout combined, per trade after costs


def colorize(msg):
    """Put each market's colour dot in front of its symbol (MGC, MGCZ6 ...) so markets are easy to tell apart."""
    import re
    return re.sub(r"(?<![🟡🔵🟢🟣🟤⚪🟠\w])(MGC|MNQ|MYM|M2K|MCL|ZN|MBT)(?=\w*)", lambda m: COLOR[m.group(1)] + m.group(1), msg)


def send_photo(png, caption=""):
    try:
        import requests
        cfg = json.loads(TG_FILE.read_text())
        r = requests.post(f"https://api.telegram.org/bot{cfg['bot_token']}/sendPhoto",
                          data={"chat_id": cfg["chat_id"], "caption": colorize(caption)[:1000]},
                          files={"photo": ("chart.png", png, "image/png")}, timeout=30)
        if not r.ok:
            print("telegram photo error:", r.status_code, r.text[:200])
    except Exception as e:
        log_event("chart_error", "", repr(e)[:200])


def send_market_chart(ib, state):
    try:
        from discipline_bot import charts
        for sym, spec in C.INSTRUMENTS.items():           # always fetch fresh bars, so charts work any time
            if spec.get("entries", True):
                try:
                    BARS_BY_SYM[sym] = history(ib, front_contract(ib, sym))[:-1][-200:]
                except Exception as e:
                    log_event("chart_error", sym, repr(e)[:150])
        stops = {t.order.orderId: t.order.trailStopPrice or t.order.auxPrice for t in ib.reqAllOpenOrders()}
        lots = {k: [dict(l, stop_now=stops.get(l.get("stop_id"))) for l in v] for k, v in state.get("lots", {}).items()}
        upnl = {}
        try:
            for it in ib.portfolio():
                if it.position:
                    upnl[it.contract.symbol] = upnl.get(it.contract.symbol, 0) + (it.unrealizedPNL or 0)
        except Exception:
            pass
        for sym, png in charts.market_chart(BARS_BY_SYM, lots, charts.read_trades(TRADES), open_pnl=upnl).items():
            L_ = lots.get(sym, [])
            if L_:
                u = upnl.get(sym)
                cap = (f"{sym}: OPEN, {len(L_)} lot(s) {'LONG' if L_[0]['side'] == 'BUY' else 'SHORT'}"
                       + (f", open P&L {'+' if u >= 0 else '−'}${abs(u):,.0f}" if u is not None else "")
                       + ". Green/red tags = your entries (L1, L2…), dark red = their stops.")
            else:
                cap = f"{sym}: no open position. NOW tag = current price; grey = 24h high/low; purple = Fibonacci."
            send_photo(png, cap)
    except Exception as e:
        log_event("chart_error", "", repr(e)[:200])


KEYBOARD = {"keyboard": [[{"text": "📊 Status"}, {"text": "🏁 Summary"}], [{"text": "📈 Chart"}, {"text": "📅 Calendar"}]],
            "resize_keyboard": True, "is_persistent": True}


def setup_telegram_buttons(state):
    """Big tap buttons under the chat box + the '/' menu, so nothing has to be typed."""
    try:
        import requests
        cfg = json.loads(TG_FILE.read_text())
        base = f"https://api.telegram.org/bot{cfg['bot_token']}"
        requests.post(f"{base}/setMyCommands", json={"commands": [
            {"command": "status", "description": "Positions now + charts"},
            {"command": "summary", "description": "Closed-trade scoreboard"},
            {"command": "chart", "description": "Charts only"},
            {"command": "calendar", "description": "This week's big US releases"},
            {"command": "quant", "description": "Quant book (ETF trend): holdings and value"},
            {"command": "close_all", "description": "CLOSE all bot positions now + pause"},
            {"command": "close_mnq", "description": "Close Nasdaq (MNQ) now + pause"},
            {"command": "close_mgc", "description": "Close gold (MGC) now + pause"},
            {"command": "close_mym", "description": "Close Dow (MYM) now + pause"},
            {"command": "close1_mnq", "description": "Close ONE Nasdaq lot (newest), keep the rest"},
            {"command": "close1_mgc", "description": "Close ONE gold lot (newest), keep the rest"},
            {"command": "close1_mym", "description": "Close ONE Dow lot (newest), keep the rest"},
            {"command": "mode_paper", "description": "Switch bot to PAPER trading"},
            {"command": "mode_live", "description": "Switch bot to LIVE trading"},
            {"command": "pause", "description": "Pause new entries (stops stay on)"},
            {"command": "resume", "description": "Resume new entries"}]}, timeout=15)
        # 'Menu' button on the LEFT of the message box (no buttons taking up screen space)
        requests.post(f"{base}/setChatMenuButton", json={"chat_id": cfg["chat_id"],
                      "menu_button": {"type": "commands"}}, timeout=15)
        if not state.get("keyboard_v2"):
            requests.post(f"{base}/sendMessage", json={"chat_id": cfg["chat_id"], "reply_markup": {"remove_keyboard": True},
                          "text": "Buttons moved: tap ☰ Menu on the LEFT of the message box for Status, Summary, Chart, Calendar."},
                          timeout=15)
            state["keyboard_v2"] = True
    except Exception as e:
        log_event("telegram_error", "", f"buttons: {e!r}"[:200])


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
    st = json.loads(STATE.read_text()) if STATE.exists() else {"open": {}, "processed_commands": [], "stops": {}}
    lots = st.setdefault("lots", {})
    for sym, tr in st.get("open", {}).items():          # migrate single-position records to lots
        if sym not in lots:
            tr.update(stop_id=st.get("stops", {}).get(sym, {}).get("orderId"), lot=1)
            lots[sym] = [tr]
    st["open"] = {}
    return st


def lots_of(state, sym):
    return state.setdefault("lots", {}).setdefault(sym, [])


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
    git("add", C.JOURNAL_DIR, C.COMMAND_FILE, *[d for d in ("data/history", "data/quant") if (ROOT / d).exists()])
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


def _easter(y):
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    return dt.date(y, month, (h + l - 7 * m + 114) % 31 + 1)


def us_holiday_or_half_day(t):
    """NYSE holiday or early-close day (New York date), from the exchange's standing rules. Returns its name or None."""
    from zoneinfo import ZoneInfo
    d = t.astimezone(ZoneInfo("America/New_York")).date()
    y = d.year

    def nth(month, weekday, n):          # n-th weekday of a month; n = -1 means the last one
        if n > 0:
            first = dt.date(y, month, 1)
            return first + dt.timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
        last = dt.date(y, month + 1, 1) - dt.timedelta(days=1) if month < 12 else dt.date(y, 12, 31)
        return last - dt.timedelta(days=(last.weekday() - weekday) % 7)

    def observed(day):                   # Saturday -> Friday, Sunday -> Monday
        return day - dt.timedelta(days=1) if day.weekday() == 5 else day + dt.timedelta(days=1) if day.weekday() == 6 else day

    thanks = nth(11, 3, 4)
    hols = {observed(dt.date(y, 1, 1)): "New Year holiday", nth(1, 0, 3): "Martin Luther King Day holiday",
            nth(2, 0, 3): "Presidents Day holiday", _easter(y) - dt.timedelta(days=2): "Good Friday holiday",
            nth(5, 0, -1): "Memorial Day holiday", observed(dt.date(y, 6, 19)): "Juneteenth holiday",
            observed(dt.date(y, 7, 4)): "Independence Day holiday", nth(9, 0, 1): "Labor Day holiday",
            thanks: "Thanksgiving holiday", observed(dt.date(y, 12, 25)): "Christmas holiday"}
    if d in hols:
        return hols[d]
    halves = {thanks + dt.timedelta(days=1): "half-day after Thanksgiving", dt.date(y, 12, 24): "Christmas Eve half-day",
              dt.date(y, 7, 3): "half-day before Independence Day"}
    if d in halves and d.weekday() < 5:
        return halves[d]
    return None


def check_entry(ib, state, sym, qty, atr, add=False, price=None):
    """Return None if the entry may go ahead, else the reason it is blocked. add=True: scaling in."""
    spec = C.INSTRUMENTS.get(sym)
    if spec is None:
        return f"{sym} is not on the instrument list"
    held = {p.contract.symbol: p.position for p in ib.positions() if p.position}
    if sym in held and not add:
        return f"already holding {sym}"
    if add and len(lots_of(state, sym)) >= C.MAX_LOTS:
        return f"already {C.MAX_LOTS} lots in {sym} (fully scaled in)"
    pnl = todays_realised_pnl(ib)    # hard daily loss limit: always on (Prof, 27 Nov 2026)
    if pnl <= -C.DAILY_LOSS_LIMIT_USD:
        return f"daily loss limit hit (${pnl:,.0f} today, limit ${C.DAILY_LOSS_LIMIT_USD:,})"
    hol = C.HOLIDAY_NO_ENTRIES and us_holiday_or_half_day(now())   # 27 Nov 2026 owner approved: no new entries on thin US holiday sessions
    if hol:
        return f"no new entries today: US {hol}"
    if add and C.ADD_ONLY_WHEN_PREV_LOT_UP_ATR:   # 27 Nov 2026 owner approved: don't stack lots near the top
        prev = lots_of(state, sym)[-1]
        sign = 1 if prev["side"] == "BUY" else -1
        if price is None or sign * (price - float(prev["entry_price"])) < C.ADD_ONLY_WHEN_PREV_LOT_UP_ATR * float(prev["atr"]):
            return (f"not adding a lot: the previous lot ({prev['entry_price']}) is not yet "
                    f"{C.ADD_ONLY_WHEN_PREV_LOT_UP_ATR} ATR in profit")
    if not C.GUARDRAILS_ON:          # 26 Nov 2026: Prof's call, no other entry-blocking limits on the paper account
        return None
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
    contract = front_contract(ib, sym)
    atr, price = atr_and_last(ib, contract)
    lots = lots_of(state, sym)
    add = bool(lots) and lots[0]["side"] == side
    why = check_entry(ib, state, sym, qty, atr, add=add, price=price)
    if why:
        log_event("blocked", sym, why)
        send_telegram(f"🛑 discipline_bot blocked {side} {qty} {sym}: {why}")
        return
    sign = 1 if side == "BUY" else -1
    stop = round_tick(price - sign * C.INITIAL_STOP_ATR * atr, spec["tick"])
    n = len(lots) + 1                              # lot number: newer lots trail tighter and peel off first
    trail = round_tick(C.LOT_TRAILS_ATR[min(n, len(C.LOT_TRAILS_ATR)) - 1] * atr, spec["tick"])
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
    lots.append(dict(trade_id=tid, symbol=sym, side=side, qty=qty, source="bot", lot=n,
                     entry_time=now().isoformat(timespec="seconds"), entry_price=fill,
                     atr=round(atr, 4), initial_stop=stop, trail_dist=trail, risk_usd=round(risk, 2),
                     note=cmd.get("note", ""), stop_id=t_child.order.orderId))
    state["stops"].setdefault(sym, {"orderId": t_child.order.orderId, "trail": trail})
    log_event("entry", sym, f"lot {n} {side} {qty} @ {fill} | stop {stop} | trail {trail} | risk ${risk:,.0f}")
    send_telegram(f"✅ <b>{sym} {'LONG' if side == 'BUY' else 'SHORT'} lot {n} of {C.MAX_LOTS}</b> @ {fill}\n"
                  f"   stop {stop} · trails {trail} ({C.LOT_TRAILS_ATR[min(n, len(C.LOT_TRAILS_ATR)) - 1]}×ATR) · risk ${risk:,.0f}",
                  html=True)


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
        if t and known and t.order.orderType == "TRAIL" and t.order.auxPrice and t.order.auxPrice > known["trail"] * 1.001:
            t.order.auxPrice = known["trail"]
            ib.placeOrder(contract, t.order)
            log_event("override_restored", sym, f"trail widened to {t.order.auxPrice}; restored to {known['trail']}")
            send_telegram(f"⚠️ {sym} stop was widened by hand — restored. Widening a stop is loss aversion talking.")
            continue
        if t:
            if not lots_of(state, sym):          # e.g. lost after a restart: start journaling it again
                lots_of(state, sym).append(dict(stop_id=t.order.orderId, lot=1, trade_id=uuid.uuid4().hex[:8], symbol=sym, side="BUY" if p.position > 0 else "SELL",
                                          qty=abs(p.position), source="adopted", entry_time=now().isoformat(timespec="seconds"),
                                          entry_price=round(p.avgCost / spec["multiplier"], 2), atr="", initial_stop=t.order.trailStopPrice,
                                          trail_dist=t.order.auxPrice,
                                          risk_usd=round(abs(p.avgCost / spec["multiplier"] - (t.order.trailStopPrice or 0)) * spec["multiplier"] * abs(p.position), 2) or 1))
                state["stops"].setdefault(sym, {"orderId": t.order.orderId, "trail": t.order.auxPrice})
                log_event("adopted", sym, f"{p.position} @ {lots_of(state, sym)[0]['entry_price']} with existing {t.order.orderType} stop")
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
        if not lots_of(state, sym):
            lots_of(state, sym).append(dict(trade_id=uuid.uuid4().hex[:8], symbol=sym, side=side, qty=abs(p.position),
                                      source="manual-guarded", entry_time=now().isoformat(timespec="seconds"),
                                      entry_price=entry, atr=round(atr, 4), initial_stop=stop, trail_dist=trail,
                                      risk_usd=round(C.INITIAL_STOP_ATR * atr * spec["multiplier"] * abs(p.position), 2),
                                      stop_id=tr.order.orderId, lot=1))
        log_event(reason, sym, f"stop attached at {stop}, trail {trail}")
        send_telegram(f"🛡️ {sym} position had no stop — attached {stop}, trailing {trail}.")


TICKS, PRICES = {}, {}


def tighten_all(ib, state, sym, k):
    """Tighten every lot's trailing stop in sym to k x ATR (never loosens)."""
    lots = lots_of(state, sym)
    if not lots:
        return []
    spec = C.INSTRUMENTS[sym]
    done = []
    orders = {t.order.orderId: t for t in ib.reqAllOpenOrders()}
    for lot in lots:
        t = orders.get(lot.get("stop_id"))
        new = round_tick(k * float(lot["atr"]), spec["tick"])
        if t and t.order.orderType == "TRAIL" and (t.order.auxPrice or 0) > new:
            t.order.auxPrice = new
            ib.placeOrder(t.contract, t.order)
            lot["trail_dist"] = new
            done.append(f"L{lot.get('lot', 1)} {new:g}")
    if done and sym in state.get("stops", {}):
        state["stops"][sym]["trail"] = min(state["stops"][sym].get("trail") or 1e9, *[float(l["trail_dist"]) for l in lots])
    return done


def lock_profits(ib, state, sym, px):
    """Owner's rule (27 Nov 2026): the moment a lot is in profit, lock it in and trail it up; a winner must never turn
    into a loser. Once a lot is LOCK_TRIGGER_ATR in profit its IBKR TRAIL stop is replaced by a plain STP that the bot
    ratchets every 5 s: stop = entry + max(floor, peak - LOCK_TRAIL_ATR x ATR, LOCK_KEEP_FRACTION x peak). Never moves back."""
    spec = C.INSTRUMENTS[sym]
    tick = spec["tick"]
    orders = None
    for lot in lots_of(state, sym):
        try:
            atr, entry = float(lot["atr"]), float(lot["entry_price"])
        except (TypeError, ValueError, KeyError):
            continue
        sign = 1 if lot["side"] == "BUY" else -1
        gain = sign * (px - entry)
        peak = max(float(lot.get("peak_gain") or 0), gain)
        lot["peak_gain"] = peak
        if peak < C.LOCK_TRIGGER_ATR * atr:
            continue
        keep = max(C.LOCK_FLOOR_ATR * atr, 4 * tick, peak - C.LOCK_TRAIL_ATR * atr, C.LOCK_KEEP_FRACTION * peak)
        new_stop = round_tick(entry + sign * keep, tick)
        if sign * (px - new_stop) < 2 * tick:            # never place the stop through the market
            new_stop = round_tick(px - sign * 2 * tick, tick)
        old = lot.get("lock_stop")
        if old is not None and sign * (new_stop - float(old)) < max(tick, 0.1 * atr):
            continue                                     # only ratchet forward, in steps of >= 0.1 ATR
        if orders is None:
            orders = {t.order.orderId: t for t in ib.reqAllOpenOrders()}
        t = orders.get(lot.get("stop_id"))
        if not t or t.orderStatus.status in ("Filled", "Cancelled", "ApiCancelled", "Inactive"):
            continue
        if t.order.orderType == "STP":
            if sign * (new_stop - float(t.order.auxPrice)) <= 0:
                continue
            t.order.auxPrice = new_stop
            ib.placeOrder(t.contract, t.order)
        else:                                            # first lock: TRAIL -> bot-managed STP
            ib.cancelOrder(t.order)
            o = Order(action="SELL" if sign > 0 else "BUY", totalQuantity=int(lot.get("qty") or 1), orderType="STP",
                      auxPrice=new_stop, tif="GTC", outsideRth=True, orderRef=C.ORDER_REF)
            nt = ib.placeOrder(front_contract(ib, sym), o)
            lot["stop_id"] = nt.order.orderId
            locked_usd = keep * spec["multiplier"] * int(lot.get("qty") or 1)
            log_event("profit_lock", sym, f"lot {lot.get('lot', 1)} up {gain:g}: stop {new_stop} locks ~${locked_usd:,.0f}")
            send_telegram(f"🔒 {sym} lot {lot.get('lot', 1)} is in profit. Stop moved to {new_stop}: this lot can no longer "
                          f"lose (locks about ${locked_usd:,.0f}). It now follows price.")
        lot["lock_stop"] = new_stop
        lot["trail_dist"] = round(abs(px - new_stop), 4)


def protect(ib, state):
    """Every 5 s: (1) shock exit: price moves > SHOCK_ATR x ATR against an open position within 5 minutes ->
    tighten all its stops to 1 ATR; (2) 15 min before a high-impact US release -> tighten to PRE_RELEASE_ATR."""
    for sym, lots in list(state.get("lots", {}).items()):
        if not lots:
            continue
        if sym not in TICKS:
            TICKS[sym] = ib.reqMktData(front_contract(ib, sym), "", False, False)
        tk = TICKS[sym]
        px = tk.last if tk.last == tk.last and tk.last else tk.marketPrice()
        if not px or px != px:
            continue
        try:
            lock_profits(ib, state, sym, px)
        except Exception as e:
            log_event("lock_error", sym, repr(e)[:200])
        hist = PRICES.setdefault(sym, [])
        hist.append((now(), px))
        PRICES[sym] = hist = [(t, p) for t, p in hist if (now() - t).total_seconds() <= 300]
        atr = float(lots[0]["atr"])
        adverse = (max(p for _, p in hist) - px) if lots[0]["side"] == "BUY" else (px - min(p for _, p in hist))
        last = state.setdefault("shock_at", {}).get(sym)
        if adverse > C.SHOCK_ATR * atr and (not last or (now() - dt.datetime.fromisoformat(last)).total_seconds() > 3600):
            state["shock_at"][sym] = now().isoformat(timespec="seconds")
            done = tighten_all(ib, state, sym, 1.0)
            log_event("shock", sym, f"{adverse:g} against in 5 min (> {C.SHOCK_ATR} ATR); tightened {done}")
            send_telegram(f"⚡ {sym} moved {adverse:g} points against you in 5 minutes. Stops tightened to 1 ATR: "
                          f"{', '.join(done) or 'already tight'}.")
    for ev in CALENDAR:
        mins = (dt.datetime.fromisoformat(ev["time_utc"]) - now()).total_seconds() / 60
        key = ev["time_utc"] + ev["name"]
        if 0 < mins <= 15 and key not in state.setdefault("pre_tightened", []):
            state["pre_tightened"].append(key)
            msgs = []
            for sym, lots in state.get("lots", {}).items():
                if lots:
                    done = tighten_all(ib, state, sym, C.PRE_RELEASE_ATR)
                    if done:
                        msgs.append(f"{sym}: {', '.join(done)}")
            if msgs:
                log_event("pre_release_tighten", "", f"{ev['name']}: {msgs}")
                send_telegram(f"📅 {ev['name']} in {mins:.0f} min: stops tightened to {C.PRE_RELEASE_ATR} ATR. " + "; ".join(msgs))


def close_one_lot(ib, state, sym):
    """Scale out: close only the NEWEST lot (cancel its own stop, 1 contract at market). The other lots keep running."""
    lots = lots_of(state, sym)
    if not lots:
        send_telegram(f"{sym}: no bot position to scale out of.")
        return
    lot = lots[-1]
    for t in ib.reqAllOpenOrders():
        if t.order.orderId == lot.get("stop_id"):
            ib.cancelOrder(t.order)
    ib.sleep(1)
    o = MarketOrder("SELL" if lot["side"] == "BUY" else "BUY", 1)
    o.orderRef = C.ORDER_REF
    o.outsideRth = True
    ib.placeOrder(front_contract(ib, sym), o)
    log_event("scale_out", sym, f"closing lot {lot.get('lot')} at market (manual)")
    send_telegram(f"✂️ {sym}: closing lot {lot.get('lot')} of {len(lots)} at market. "
                  f"{len(lots) - 1} lot(s) keep running with their stops.")


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
    """Each lot closes on its own: when its stop order is no longer working (filled, or cancelled by a flatten),
    it is journaled with its own closing fill."""
    open_ids = {t.order.orderId for t in ib.reqAllOpenOrders()} | {t.order.orderId for t in ib.openTrades()}
    held = {p.contract.symbol: p.position for p in ib.positions() if p.position}
    missing = state.setdefault("missing_since", {})
    used = set(state.setdefault("used_fills", [])[-200:])
    for sym, lots in list(state.get("lots", {}).items()):
        for tr in list(lots):
            key = tr["trade_id"]
            if tr.get("stop_id") in open_ids and sym in held:
                missing.pop(key, None)
                continue
            age = (now() - dt.datetime.fromisoformat(tr["entry_time"])).total_seconds()
            if sym not in force:
                if age < 180:
                    continue
                missing.setdefault(key, now().isoformat(timespec="seconds"))
                if (now() - dt.datetime.fromisoformat(missing[key])).total_seconds() < 60:
                    continue
            entry_t = dt.datetime.fromisoformat(tr["entry_time"])
            spec = C.INSTRUMENTS[sym]
            want = "SLD" if tr["side"] == "BUY" else "BOT"
            fills = sorted((f for f in ib.fills() if f.contract.symbol == sym and f.time >= entry_t
                            and f.execution.side == want and f.execution.execId not in used), key=lambda f: f.time)
            own = [f for f in fills if f.execution.orderId == tr.get("stop_id")]
            last = (own or fills or [None])[-1]
            if last is None:
                if sym in held and tr.get("stop_id") in open_ids:
                    continue
                if sym in held:          # stop vanished but position still there: guardian re-attaches; keep waiting
                    continue
                log_event("position_missing", sym, f"lot {tr.get('lot')}: no closing fill found; dropped from journal")
                lots.remove(tr); missing.pop(key, None)
                continue
            used.add(last.execution.execId)
            lots.remove(tr); missing.pop(key, None)
            exit_price = last.execution.avgPrice
            reason = state.get("pending_reason", {}).get(sym) or (
                "trailing_stop" if last.execution.orderId == tr.get("stop_id") else "other")
            sign = 1 if tr["side"] == "BUY" else -1
            pnl = sign * (exit_price - float(tr["entry_price"])) * spec["multiplier"] * int(tr["qty"])
            hours = (now() - entry_t).total_seconds() / 3600
            tr.update(exit_time=now().isoformat(timespec="seconds"), exit_price=exit_price, exit_reason=reason,
                      pnl_usd=round(pnl, 2), r_multiple=round(pnl / float(tr["risk_usd"]), 3), hours_held=round(hours, 1))
            write_trade(tr)
            log_event("exit", sym, f"lot {tr.get('lot')} {reason} @ {exit_price} | P&L ${pnl:,.0f} | {tr['r_multiple']}R | {hours:.1f}h")
            et = entry_t.astimezone(SGT)
            send_telegram(f"🏁 <b>{sym} {'LONG' if sign > 0 else 'SHORT'} lot {tr.get('lot', 1)} closed</b> · {rule_of(tr)}\n"
                          f"   opened {et:%H:%M} SGT @ {tr['entry_price']} → closed @ {exit_price} ({reason.replace('_', ' ')})\n"
                          f"   {'✅' if pnl >= 0 else '🔻'} <b>{'+' if pnl >= 0 else '−'}${abs(pnl):,.0f}</b> · {tr['r_multiple']:+.2f}R · held {hours:.1f}h"
                          + (f" · {len(lots)} lot(s) still on" if lots else ""), html=True)
        if not lots:
            state["lots"].pop(sym, None); state["stops"].pop(sym, None)
            state.get("pending_reason", {}).pop(sym, None)
    state["used_fills"] = list(used)[-200:]


# ── entry signals (alerts only) ───────────────────────────────────────────────
BARS_BY_SYM = {}   # sym -> completed 15m bars, for the Telegram charts
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


VIXC = {"t": None, "v": None}


def prior_vix(ib):
    """Yesterday's VIX close (cached 1 hour). Used by the fear guard: no stock-index longs when VIX > 22."""
    if VIXC["t"] and (now() - VIXC["t"]).total_seconds() < 3600:
        return VIXC["v"]
    try:
        vix = Index("VIX", "CBOE", "USD")
        ib.qualifyContracts(vix)
        bars = ib.reqHistoricalData(vix, endDateTime="", durationStr="5 D", barSizeSetting="1 day",
                                    whatToShow="TRADES", useRTH=True, formatDate=1)
        today = now().astimezone(dt.timezone(dt.timedelta(hours=-5))).date()   # US date
        done = [b for b in bars if b.date < today]
        VIXC.update(t=now(), v=done[-1].close if done else None)
    except Exception as e:
        log_event("vix_error", "", repr(e)[:200])
        VIXC.update(t=now(), v=None)
    return VIXC["v"]


def check_signals(ib, state):
    """Squeeze-breakout-with-trend on the last COMPLETED bar (see signals.py), for markets with entries on."""
    seen = state.setdefault("signal_bar", {})
    if state.get("paused"):
        return
    for sym, spec in C.INSTRUMENTS.items():
        if not spec.get("entries", True):
            continue
        try:
            check_one_signal(ib, state, seen, sym, spec)
        except Exception as e:
            log_event("signal_error", sym, repr(e)[:200])


def check_one_signal(ib, state, seen, sym, spec):
    from discipline_bot.signals import squeeze_signal, daybreak_signal, reversal_signal, ema_cross_signal
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
    BARS_BY_SYM[sym] = done[-200:]
    H, L, Cl = [b.high for b in done], [b.low for b in done], [b.close for b in done]
    sign, reason = squeeze_signal(H, L, Cl)
    rule = "squeeze"
    if not sign and not reason.startswith("warming"):
        sign2, reason2 = daybreak_signal(H, L, Cl, *C.DAYBREAK_TREND.get(sym, (200, 800)), allow_override=C.DAYBREAK_OVERRIDE)
        if sign2:
            sign, reason, rule = sign2, reason2, "24h breakout"
        else:
            reason = f"{reason}; {reason2}"
            if sym in C.REVERSAL_MARKETS:
                sign3, reason3 = reversal_signal(H, L, Cl)
                if sign3:
                    sign, reason, rule = sign3, reason3, "reversal"
                else:
                    reason = f"{reason}; {reason3}"
            if not sign and sym in C.EMA_CROSS_MARKETS:
                sign4, reason4 = ema_cross_signal(H, L, Cl)
                if sign4:
                    sign, reason, rule = sign4, reason4, "EMA 8/21"
                else:
                    reason = f"{reason}; {reason4}"
    try:   # last 2 days of completed 15m bars, so every decision can be checked later
        with (JDIR / f"bars_{sym}.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["time_utc", "open", "high", "low", "close", "volume"])
            w.writerows((b.date.isoformat(), b.open, b.high, b.low, b.close, b.volume) for b in done[-192:])
    except Exception as e:
        log_event("bars_error", sym, repr(e)[:150])
    lo24, hi24 = min(L[-97:-1]), max(H[-97:-1])
    state.setdefault("signal_status", {})[sym] = (f"{now().astimezone(SGT):%H:%M} close {Cl[-1]:g} · 24h range "
                                                  f"{lo24:g}–{hi24:g} · {reason}")
    if not sign:
        if reason.startswith("warming"):
            log_event("signal_info", sym, reason)
        return
    if sign > 0 and spec.get("index") and rule != "reversal":
        v = prior_vix(ib)
        if v is not None and v > C.VIX_NO_INDEX_LONGS:
            state["signal_status"][sym] += f" · LONG skipped: VIX {v:.1f} > {C.VIX_NO_INDEX_LONGS} (fear guard)"
            log_event("blocked", sym, f"{rule} long skipped, prior VIX close {v:.1f} > {C.VIX_NO_INDEX_LONGS}")
            return
    side = "BUY" if sign > 0 else "SELL"
    trs = [max(b.high - b.low, abs(b.high - p.close), abs(b.low - p.close)) for p, b in zip(done[:-1], done[1:])]
    atr = sum(trs[-C.ATR_PERIOD:]) / C.ATR_PERIOD
    stop = round_tick(bar.close - sign * C.INITIAL_STOP_ATR * atr, spec["tick"])
    trail = round_tick(C.TRAIL_ATR * atr, spec["tick"])
    risk = C.INITIAL_STOP_ATR * atr * spec["multiplier"]
    held = sum(p.position for p in ib.positions() if p.contract.symbol == sym)
    if held and (held > 0) != (sign > 0):
        # signal against the open position: reverse instead of ignoring the opportunity
        send_telegram(f"🔄 <b>{sym}: new {rule} signal against the open {'LONG' if held > 0 else 'SHORT'}</b>. "
                      f"Closing it and reversing.", html=True)
        flatten(ib, state, sym, f"reversed by {rule} signal")
        for _ in range(15):
            ib.sleep(2)
            if not any(p.position for p in ib.positions() if p.contract.symbol == sym):
                break
        record_exits(ib, state, force=(sym,))
    lots = lots_of(state, sym)
    adding = bool(lots) and lots[0]["side"] == side
    why = check_entry(ib, state, sym, 1, atr, add=adding)
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
        send_telegram(f"🤖 <b>{sym} {'LONG' if sign > 0 else 'SHORT'} {'adding lot ' + str(len(lots) + 1) if adding else 'opening'}</b> · {rule} rule\n"
                      f"   {reason} (15-min close {bar.close})\n"
                      f"   stop about {stop} · trail {trail} · risk about ${risk:,.0f}", html=True)
        enter(ib, state, {"symbol": sym, "side": side, "qty": 1, "note": f"auto {({'squeeze': 'squeeze', '24h breakout': 'daybreak', 'EMA 8/21': 'emacross'}).get(rule, 'reversal')} {key}"})
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
        if not it.position or it.contract.secType != "FUT" or it.contract.symbol not in C.INSTRUMENTS:
            continue          # only the bot's futures; never the owner's stocks (the snapshot is pushed to GitHub)
        mult = C.INSTRUMENTS.get(it.contract.symbol, {"multiplier": 1})["multiplier"]
        t = protective_stop(ib, it.contract, trades)
        snap["positions"].append({
            "contract": it.contract.localSymbol, "qty": it.position, "avg_price": round(it.averageCost / mult, 2),
            "last": it.marketPrice, "unrealized_usd": round(it.unrealizedPNL, 2),
            "stop": None if not t else {"type": t.order.orderType, "trail": t.order.auxPrice,
                                        "stop_price": t.order.auxPrice if t.order.orderType == "STP" else t.order.trailStopPrice, "status": t.orderStatus.status}})
    (JDIR / "snapshot.json").write_text(json.dumps(snap, indent=2, default=str))


STRATEGY = ("<b>Strategy</b>: two entry rules, both only with the 1h trend, 15-min bars\n"
            "1) Squeeze: quiet market (40-bar band width in lowest 20%) + close beyond 40-bar high/low\n"
            "2) 24h breakout: close beyond the 24-hour high/low (catches trends already running)\n"
            "3) Reversal (Nasdaq, Dow): after a 12-ATR crash or spike, close beyond the 2-hour high/low (catches the turn)\n"
            "4) EMA 8/21 cross with the trend (gold only)\n"
            "Fear guard: no Nasdaq/Dow longs from rules 1-2 while yesterday's VIX close is above 22\n"
            "Exit = stop 1.5 ATR, trail 3 ATR, no target · entries on 🟡MGC 🔵MNQ 🟢MYM")


def rule_of(tr):
    n = (tr or {}).get("note") or ""
    return ("squeeze rule" if "squeeze" in n else "24h breakout rule" if "daybreak" in n
            else "reversal rule" if "reversal" in n else "EMA 8/21 rule" if "emacross" in n
            else "old breakout rule" if "breakout" in n else "manual")


def _esc(x):
    return str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def status_text(ib, state):
    write_snapshot(ib, state)
    snap = json.loads((JDIR / "snapshot.json").read_text())
    out = [f"📊 <b>Status {now().astimezone(SGT):%a %d %b %H:%M} SGT</b> · <b>{'🔴 LIVE' if C.IB_PORT == 4001 else 'PAPER'}</b> · auto {'ON' if C.AUTO_ENTRIES else 'OFF'}", STRATEGY, ""]
    total = 0.0
    pos = sorted(snap["positions"], key=lambda p: list(COLOR).index(p["contract"][:3]) if p["contract"][:3] in COLOR else 9)
    out.append("<b>Open positions</b>")
    for p_ in pos:
        st, u = p_["stop"], p_["unrealized_usd"] or 0
        total += u
        sym = next((k for k in COLOR if p_["contract"].startswith(k)), p_["contract"])
        out.append(f"{'📈 LONG' if p_['qty'] > 0 else '📉 SHORT'} <b>{_esc(p_['contract'])}</b> ({NAME.get(sym, '')}) · "
                   f"<i>{rule_of((state.get('lots', {}).get(sym) or [None])[0])} · {len(state.get('lots', {}).get(sym, []))} lot(s)</i>\n"
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


def summary_text(ib, state=None):
    rows = list(csv.DictReader(TRADES.open())) if TRADES.exists() else []
    def num(x):
        try:
            v = float(x)
            return None if math.isnan(v) else v
        except (TypeError, ValueError):
            return None
    isnew = lambda r: any(k in (r.get("note") or "") for k in ("squeeze", "daybreak", "reversal", "emacross"))
    new = [r for r in rows if isnew(r) and num(r.get("pnl_usd")) is not None]
    old = [r for r in rows if not isnew(r) and num(r.get("pnl_usd")) is not None]
    out = [f"🏁 <b>Performance {now().astimezone(SGT):%a %d %b %H:%M} SGT</b> · <b>{'🔴 LIVE (since 27 Nov 14:33)' if C.IB_PORT == 4001 else 'PAPER'}</b>", STRATEGY, "",
           "<b>New rules</b> (squeeze 25 Nov 11:38, 24h breakout 26 Nov 00:26, reversal 26 Nov 08:40 SGT), <b>closed</b> trades:"]
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
        if max(rs) > 0:
            out.append(f"Biggest win {max(rs):+.1f}R · losers cut at about −1R as designed")
    if state is not None:
        live = {}
        try:
            for it in ib.portfolio():
                if it.position:
                    live[it.contract.symbol] = it.unrealizedPNL
        except Exception:
            pass
        opened = [(k, v) for k, vs in state.get("lots", {}).items() for v in vs if isnew(v)]
        out.append("\n<b>New rules, open now:</b>" if opened else "\nNew rules, open now: none")
        for k, v in opened:
            u = live.get(k)
            et = dt.datetime.fromisoformat(v["entry_time"]).astimezone(SGT)
            out.append(f"{k} lot {v.get('lot', 1)} {'LONG' if v['side'] == 'BUY' else 'SHORT'} ({rule_of(v)}) since {et:%a %H:%M} @ {v['entry_price']}"
                       + (f" · {'+' if u >= 0 else '−'}${abs(u):,.0f} open" if u is not None else ""))
        olds = sorted({k for k, vs in state.get("lots", {}).items() for v in vs if not isnew(v)})
        if olds:
            out.append("<i>Still open from the old rule (stops only, no new entries): " + ", ".join(olds) + "</i>")
    out.append("Expect: about 1 win in 3; profit comes from a few big winners. Judge after 30+ trades, not days.")
    if old:
        pnl = sum(num(r["pnl_usd"]) for r in old)
        out.append(f"\n<i>Old 20-bar breakout rule (retired): {len(old)} trades · {'+' if pnl >= 0 else '−'}${abs(pnl):,.0f}</i>")
    out.append("\n/status for positions and setups")
    return "\n".join(out)



def ask_confirm(state, action, syms, question):
    """Send Yes/No buttons; nothing happens until Yes is tapped (expires after 2 minutes)."""
    import requests
    cfg = json.loads(TG_FILE.read_text())
    key = uuid.uuid4().hex[:8]
    state["pending_confirm"] = {"key": key, "action": action, "syms": syms, "time": now().isoformat(timespec="seconds")}
    requests.post(f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage", timeout=15, json={
        "chat_id": cfg["chat_id"], "text": colorize("❓ " + question),
        "reply_markup": {"inline_keyboard": [[{"text": "✅ Yes, do it", "callback_data": f"yes:{key}"},
                                              {"text": "❌ No", "callback_data": f"no:{key}"}]]}})


def handle_confirm(ib, state, cq, cfg):
    import requests
    requests.post(f"https://api.telegram.org/bot{cfg['bot_token']}/answerCallbackQuery",
                  json={"callback_query_id": cq["id"]}, timeout=15)
    ans, _, key = (cq.get("data") or "").partition(":")
    pc = state.get("pending_confirm") or {}
    if pc.get("key") != key or (now() - dt.datetime.fromisoformat(pc["time"])).total_seconds() > 120:
        send_telegram("⌛ That request expired. Send it again if you still want it.")
        return
    state.pop("pending_confirm", None)
    if ans != "yes":
        send_telegram("👍 Cancelled. Nothing was closed.")
        return
    log_event("manual_confirmed", ",".join(pc["syms"]), pc["action"])
    if pc["action"] == "mode":
        port = int(pc["syms"][0])
        C.MODE_FILE.write_text(json.dumps({"port": port, "prev": C.IB_PORT, "just_switched": True}))
        log_event("mode_switch", "", f"{mode_name(C.IB_PORT)} -> {mode_name(port)}")
        send_telegram(f"🔁 Switching to {mode_name(port)}. Back in about 15 seconds.")
        state["restart_now"] = True
        return
    if pc["action"] == "close1":
        for sy in pc["syms"]:
            close_one_lot(ib, state, sy)
    else:
        for sy in pc["syms"]:
            flatten(ib, state, sy, "manual_close_telegram")
        state["paused"] = True
        send_telegram(f"✋ Closing {', '.join(pc['syms']) or 'nothing'} at market. New entries are PAUSED until you send: resume")


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
        cq = u.get("callback_query")
        if cq:
            if str(cq.get("message", {}).get("chat", {}).get("id")) == str(cfg["chat_id"]):
                handle_confirm(ib, state, cq, cfg)
            continue
        msg = u.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != str(cfg["chat_id"]):
            log_event("telegram_cmd", "", f"ignored message from chat {msg.get('chat', {}).get('id')}")
            continue                                # only answer you
        raw = (msg.get("text") or "").strip().lower()
        if msg.get("voice") or msg.get("audio"):
            send_telegram("🎤 I can't hear voice notes. Tap a button below, or use your keyboard's mic to say "
                          "\"status\", \"summary\", \"chart\" or \"calendar\".")
            continue
        words = "".join(ch if ch.isalnum() or ch == " " else " " for ch in raw).split()
        cmd = next((c for c, keys in (("status", ("status", "position", "positions", "now")),
                                      ("summary", ("summary", "performance", "score", "scoreboard", "results")),
                                      ("chart", ("chart", "charts", "graph", "picture")),
                                      ("calendar", ("calendar", "macro", "news")),
                                      ("quant", ("quant", "etf", "etfs")),
                                      ("help", ("help", "start", "menu")))
                    if any(w in keys for w in words)), "")
        log_event("telegram_cmd", "", raw[:40])
        # manual override: "close MNQ" / "close all" / "pause" / "resume"
        syms = [w.upper() for w in words if w.upper() in C.INSTRUMENTS]
        if any(w in ("mode_paper", "mode_live") for w in words) or (("paper" in words or "live" in words) and ("mode" in words or "switch" in words)):
            port = 4002 if ("mode_paper" in words or "paper" in words) else 4001
            held = [p.contract.symbol for p in ib.positions() if p.position and p.contract.symbol in C.INSTRUMENTS]
            stale = [sy for sy in C.INSTRUMENTS if lots_of(state, sy) and sy not in held]
            if stale:                               # IBKR says flat but the journal hasn't booked the exit yet
                record_exits(ib, state, force=tuple(stale))
            if port == C.IB_PORT:
                send_telegram(f"Already on {mode_name(port)}.")
            elif held:
                send_telegram(f"⛔ You hold {', '.join(held)} on {mode_name(C.IB_PORT)}. Close it first "
                              f"(/close_{held[0].lower()}), then switch. Otherwise nobody manages that position.")
            else:
                ask_confirm(state, "mode", [str(port)], f"Switch the bot to {mode_name(port)} trading?")
            continue
        if ("close1" in words or ("close" in words and ("one" in words or "1" in words))) and syms:
            ask_confirm(state, "close1", syms, f"Close ONE lot (the newest) of {', '.join(syms)} at market?")
            continue
        if any(w in ("close", "exit", "flat", "flatten", "sell", "cover") for w in words) and (syms or "all" in words):
            targets = syms or [p.contract.symbol for p in ib.positions() if p.position and p.contract.symbol in C.INSTRUMENTS]
            ask_confirm(state, "close", targets, f"Close ALL lots of {', '.join(targets) or 'nothing'} at market and pause new entries?")
            continue
        if "pause" in words or "stop" in words:
            state["paused"] = True
            send_telegram("⏸ New entries PAUSED. Open positions keep their stops. Send: resume to restart.")
            continue
        if "resume" in words or "unpause" in words:
            state["paused"] = False
            send_telegram("▶️ New entries RESUMED.")
            continue
        text = "/" + cmd if cmd else raw
        if text in ("/status", "status", "/positions", "positions"):
            send_telegram(status_text(ib, state), html=True)
            send_market_chart(ib, state)
        elif text in ("/summary", "summary", "/performance", "performance"):
            send_telegram(summary_text(ib, state), html=True)
            try:
                from discipline_bot import charts
                isnew = lambda r: any(k in (r.get("note") or "") for k in ("squeeze", "daybreak", "reversal", "emacross"))
                send_photo(charts.performance_chart(charts.read_trades(TRADES), isnew),
                           "Scoreboard: CLOSED trades only (money already won or lost). Open positions are in /status.")
            except Exception as e:
                log_event("chart_error", "", repr(e)[:200])
        elif text in ("/quant", "quant"):
            try:
                from discipline_bot import quant
                qs, qpx, qv = quant.mark(ib)
                rows = [f"{k}: {n} sh @ {qpx.get(k, 0):.2f} = ${n * qpx.get(k, 0):,.0f}" for k, n in sorted(qs['shares'].items())]
                send_telegram(f"📐 <b>Quant book (ETF trend)</b>\nValue <b>${qv:,.0f}</b> "
                              f"({'+' if qv >= quant.QUANT_CAPITAL else '−'}${abs(qv - quant.QUANT_CAPITAL):,.0f} since start) · "
                              f"cash ${qs['cash']:,.0f}\n" + ("\n".join(rows) or "No holdings yet (first rebalance runs "
                              "in the US session 10:00-12:30 New York)") + f"\nLast rebalance week: {qs.get('last_week') or 'none yet'}", html=True)
            except Exception as e:
                send_telegram(f"Quant book error: {e!r}"[:200])
        elif text in ("/chart", "chart", "/charts", "charts"):
            send_market_chart(ib, state)
        elif text in ("/calendar", "calendar", "/macro", "macro"):
            send_telegram(calendar_text())
        elif text in ("/help", "help", "/start") or text:
            setup_telegram_buttons(state)
            send_telegram("/status — strategy, positions, stops and setups\n/summary — performance so far vs the backtest, with a chart\n/chart — 15-min charts with levels\n"
                          "/calendar — this week's high-impact US releases (SGT)")


# ── commands ──────────────────────────────────────────────────────────────────
def read_queue():
    if not QUEUE.exists():
        return []
    return json.loads(QUEUE.read_text() or "[]")


def process_commands(ib, state):
    if not state.get("queue_baseline"):
        # a fresh journal (e.g. switching to live) must NEVER replay old commands: mark everything already queued as done
        state["processed_commands"] = list({*state.get("processed_commands", []), *[c.get("id") for c in read_queue()]})
        state["queue_baseline"] = now().isoformat(timespec="seconds")
        return
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
            elif cmd["action"] == "live_check":       # read-only test of the LIVE Gateway (port 4001); no orders
                root = Path(__file__).resolve().parent.parent
                subprocess.Popen([sys.executable, "-u", "-m", "discipline_bot.live_check"], cwd=root)
                log_event("live_check_started", "", "read-only live connection test")
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
                env = dict(os.environ, **{k: str(v) for k, v in (cmd.get("env") or {}).items()})
                subprocess.Popen([sys.executable, "-u", "-m", "discipline_bot.download"], cwd=root,
                                 stdout=log, stderr=subprocess.STDOUT, env=env)
                log_event("download_started", "", f"history download started {cmd.get('env') or ''}")
            else:
                log_event("blocked", cmd.get("symbol", ""), f"unknown action {cmd['action']}")
        except Exception as e:
            log_event("error", cmd.get("symbol", ""), f"{cid}: {e}")


# ── main loop ─────────────────────────────────────────────────────────────────
BENIGN = {1100, 1101, 1102, 2103, 2104, 2105, 2106, 2107, 2108, 2109, 2119, 2157, 2158, 10167}


_LAST_BLIND = dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc)


def on_error(reqId, code, msg, contract):
    """Record IBKR errors (order rejections etc.) in the journal so they are visible remotely."""
    if code not in BENIGN:
        log_event("ibkr_error", getattr(contract, "symbol", "") if contract else "", f"{code} (req {reqId}): {msg}"[:300])
    if code == 162 and "different IP" in msg:      # account logged in elsewhere: the bot is blind, say so at once
        global _LAST_BLIND
        if (now() - _LAST_BLIND).total_seconds() > 600:
            _LAST_BLIND = now()
            send_telegram("🚫 Bot is BLIND: the trading account is logged in on another device (phone/TWS/web), so IBKR "
                          "is blocking the bot's prices. Signals cannot fire until you log out there.")


class ModeSwitch(Exception):
    pass


def mode_name(port):
    return "LIVE" if int(port) == 4001 else "PAPER"


def read_mode():
    try:
        return json.loads(C.MODE_FILE.read_text())
    except Exception:
        return {}


def connect():
    ib = IB()
    ib.errorEvent += on_error
    try:
        ib.connect(C.IB_HOST, C.IB_PORT, clientId=C.CLIENT_ID)
    except Exception:
        m = read_mode()
        if m.get("just_switched"):              # switched from Telegram but that Gateway is not running: go back
            C.MODE_FILE.write_text(json.dumps({"port": m["prev"]}))
            send_telegram(f"⚠️ Could not reach the {mode_name(C.IB_PORT)} Gateway on the Lenovo (port {C.IB_PORT}). "
                          f"Log it in there and try again. Staying on {mode_name(m['prev'])}.")
            raise ModeSwitch()
        raise
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
    # No "online" message: Telegram is for trades only. Restarts are silent; outages over 15 min are reported.
    while True:
        try:
            if ib is None or not ib.isConnected():
                try:
                    ib = connect()
                except ModeSwitch:
                    return                              # run_bot.bat restarts on the previous mode
                log_event("connected", "", ",".join(a[:2] + "…" + a[-3:] for a in ib.managedAccounts()))
                m = read_mode()
                if m.pop("just_switched", None):
                    C.MODE_FILE.write_text(json.dumps(m))
                    send_telegram(("🔴 Now trading LIVE money." if C.IB_PORT == 4001 else "📝 Now on PAPER trading.")
                                  + " Send /status to check.")
                setup_telegram_buttons(state)
                if not state.get("charts_ready"):
                    try:
                        from discipline_bot import charts
                        charts._plt()
                        state["charts_ready"] = True
                        log_event("charts_ready", "", "matplotlib available")
                    except Exception as e:
                        log_event("chart_error", "", repr(e)[:400])
                if state.pop("outage_alerted", None):
                    send_telegram("✅ Connection back. Bot is trading again.")
                state.pop("down_since", None)
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
            try:
                from discipline_bot import quant
                quant.rebalance(ib, log_event, send_telegram)
            except Exception as e:
                log_event("quant_error", "", repr(e)[:300])
            refresh_calendar(state)
            calendar_alerts(state)
            telegram_commands(ib, state)
            n = now()
            bar_key = n.strftime("%Y%m%d%H") + f"{n.minute // C.BAR_MINUTES:02d}"
            if C.SIGNAL_ALERTS and n.minute % C.BAR_MINUTES < 5 and state.get("signal_checked") != bar_key:
                state["signal_checked"] = bar_key
                check_signals(ib, state)
                write_snapshot(ib, state)
            if C.GUARD_MANUAL_POSITIONS or state.get("lots"):
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
            down = state.setdefault("down_since", now().isoformat(timespec="seconds"))
            if not state.get("outage_alerted") and (now() - dt.datetime.fromisoformat(down)).total_seconds() > 900:
                state["outage_alerted"] = True    # only real outages (>15 min); the 06:15 Gateway restart stays silent
                send_telegram(f"⚠️ Bot can't reach IB Gateway for 15+ minutes. Check the Lenovo. ({e!r})"[:300])
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
        # between full loops, answer Telegram every 5 seconds (close/pause/status feel instant)
        until = time.time() + C.LOOP_SECONDS
        while time.time() < until:
            if ib is not None and ib.isConnected():
                try:
                    telegram_commands(ib, state)
                except Exception as e:
                    log_event("telegram_error", "", repr(e)[:200])
                if state.pop("restart_now", None):
                    save_state(state)
                    git_push_journal()
                    ib.disconnect()
                    return                              # run_bot.bat restarts on the new mode
                try:
                    protect(ib, state)
                except Exception as e:
                    log_event("protect_error", "", repr(e)[:200])
                ib.sleep(5)
            else:
                time.sleep(5)


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
