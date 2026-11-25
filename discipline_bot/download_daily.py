"""
discipline_bot/download_daily.py: daily history for the ETF/equity and options research.

Downloads up to 20 years of daily bars (adjusted for splits and dividends) for a universe of ETFs, plus
the VIX index (used to model option prices). Own client id (CLIENT_ID+2) so the trading bot keeps running.
Started by the bot on {"action": "download_daily"}, or by hand: py -m discipline_bot.download_daily
Output: data/history/daily/<SYM>.csv (+ progress.json).
"""
import csv, datetime as dt, json, time
from pathlib import Path
from ib_async import IB, Stock, Index
from discipline_bot import config as C

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "history" / "daily"
ETFS = ["SPY", "QQQ", "IWM", "DIA", "GLD", "SLV", "TLT", "IEF", "SHY", "HYG", "LQD", "UUP", "USO",
        "EFA", "EEM", "VNQ", "SMH", "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC"]


def progress(**kw):
    p = OUT / "progress.json"
    d = json.loads(p.read_text()) if p.exists() else {}
    d.update(kw, updated_utc=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
    p.write_text(json.dumps(d, indent=2))


def fetch(ib, contract, what):
    for dur in ("20 Y", "10 Y", "5 Y"):
        bars = ib.reqHistoricalData(contract, endDateTime="", durationStr=dur, barSizeSetting="1 day",
                                    whatToShow=what, useRTH=True, formatDate=1)
        time.sleep(11)
        if bars:
            return bars, dur
    return [], None


def main():
    from discipline_bot.bot import send_telegram
    OUT.mkdir(parents=True, exist_ok=True)
    ib = IB()
    ib.connect(C.IB_HOST, C.IB_PORT, clientId=C.CLIENT_ID + 2)
    jobs = [(s, Stock(s, "SMART", "USD"), "ADJUSTED_LAST") for s in ETFS] + [("VIX", Index("VIX", "CBOE"), "TRADES")]
    summary = {}
    for sym, con, what in jobs:
        try:
            ib.qualifyContracts(con)
            bars, dur = fetch(ib, con, what)
            with open(OUT / f"{sym}.csv", "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["date", "open", "high", "low", "close", "volume"])
                w.writerows((b.date, b.open, b.high, b.low, b.close, b.volume) for b in bars)
            summary[sym] = f"{len(bars)} bars ({dur})" if bars else "no data"
        except Exception as e:
            summary[sym] = f"error {e!r}"[:200]
        progress(**{sym: summary[sym]})
    ib.disconnect()
    progress(finished=True)
    ok = sum(1 for v in summary.values() if "bars" in v)
    send_telegram(f"📥 Daily history finished: {ok}/{len(jobs)} symbols downloaded.")


if __name__ == "__main__":
    main()
