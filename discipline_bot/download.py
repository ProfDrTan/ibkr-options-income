"""
discipline_bot/download.py — build the research dataset.

Downloads ~2 years of 15-minute bars (24h sessions) for every market in config.INSTRUMENTS,
stitched from each contract's own front-month window (no back-adjustment; the roll date is in
the 'contract' column so research never holds a trade across a roll).

Runs as its own process with its own IBKR client id, so the trading bot keeps running.
Started by the bot when a {"action": "download_history"} command arrives, or by hand:
    py -m discipline_bot.download
Output: data/history/<SYM>_15m.csv.gz (+ progress.json); the bot's journal push uploads them.
"""
import csv, datetime as dt, gzip, json, sys, time
from pathlib import Path

from ib_async import IB, Future

from discipline_bot import config as C

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "history"
YEARS = 2
CHUNK = "10 D"          # per request; IBKR pacing allows ~60 historical requests per 10 minutes
PAUSE = 11              # seconds between requests to stay under that limit
ACTIVE_MONTHS = {"MGC": {2, 4, 6, 8, 10, 12}}   # gold: only the liquid even months


def utc(s):
    return dt.datetime.strptime(s[:8], "%Y%m%d").replace(tzinfo=dt.timezone.utc)


def progress(**kw):
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "progress.json"
    d = json.loads(p.read_text()) if p.exists() else {}
    d.update(kw, updated_utc=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
    p.write_text(json.dumps(d, indent=2))


def contracts(ib, sym, spec):
    det = ib.reqContractDetails(Future(symbol=sym, exchange=spec["exchange"], includeExpired=True))
    cons = {d.contract.conId: d.contract for d in det}.values()
    keep = [c for c in cons if sym not in ACTIVE_MONTHS
            or int(c.lastTradeDateOrContractMonth[4:6]) in ACTIVE_MONTHS[sym]]
    return sorted(keep, key=lambda c: c.lastTradeDateOrContractMonth)


def download_symbol(ib, sym, spec):
    start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=365 * YEARS)
    now = dt.datetime.now(dt.timezone.utc)
    rows, prev_end = [], None
    for c in contracts(ib, sym, spec):
        end = utc(c.lastTradeDateOrContractMonth) - dt.timedelta(days=spec["roll_days"])
        begin = prev_end or (end - dt.timedelta(days=120))
        prev_end = end
        if end < start:
            continue
        begin, end = max(begin, start), min(end, now)
        if begin >= end:
            continue
        c.includeExpired = True
        cursor = end
        while cursor > begin:
            bars = ib.reqHistoricalData(c, endDateTime=cursor.strftime("%Y%m%d-%H:%M:%S"), durationStr=CHUNK,
                                        barSizeSetting="15 mins", whatToShow="TRADES", useRTH=False, formatDate=2)
            time.sleep(PAUSE)
            if not bars:
                break
            for b in bars:
                t = b.date if isinstance(b.date, dt.datetime) else dt.datetime.combine(b.date, dt.time())
                t = t.astimezone(dt.timezone.utc) if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
                if begin <= t < end:
                    rows.append((t.isoformat(), b.open, b.high, b.low, b.close, b.volume, c.localSymbol))
            first = bars[0].date
            first = first if isinstance(first, dt.datetime) else dt.datetime.combine(first, dt.time())
            first = first.astimezone(dt.timezone.utc) if first.tzinfo else first.replace(tzinfo=dt.timezone.utc)
            if first >= cursor:
                break
            cursor = first - dt.timedelta(seconds=1)
        progress(**{sym: f"{c.localSymbol} done, {len(rows)} bars so far"})
    rows = sorted(set(rows))
    with gzip.open(OUT / f"{sym}_15m.csv.gz", "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_utc", "open", "high", "low", "close", "volume", "contract"])
        w.writerows(rows)
    return len(rows)


def main():
    from discipline_bot.bot import send_telegram
    ib = IB()
    ib.connect(C.IB_HOST, C.IB_PORT, clientId=C.CLIENT_ID + 1)
    OUT.mkdir(parents=True, exist_ok=True)
    summary = {}
    for sym, spec in C.INSTRUMENTS.items():
        try:
            n = download_symbol(ib, sym, spec)
            summary[sym] = n
            progress(**{sym: f"complete: {n} bars"})
        except Exception as e:
            summary[sym] = f"error {e!r}"[:200]
            progress(**{sym: summary[sym]})
    ib.disconnect()
    progress(finished=True)
    send_telegram("📥 History download finished: " + ", ".join(f"{k} {v}" for k, v in summary.items()))


if __name__ == "__main__":
    sys.exit(main())
