"""
discipline_bot/live_check.py: READ-ONLY test of the LIVE IB Gateway connection. Places no orders, ever.

Needs a second IB Gateway on the Lenovo logged in to the LIVE account (API port 4001, "Read-Only API" may stay ON).
Started by the paper bot on {"action": "live_check"} in commands/queue.json, or by hand: py -m discipline_bot.live_check
Reports to Telegram and data/discipline/live_check.json: account id, net liquidation, available funds, futures
permissions (can it see MNQ/MGC/MYM contracts and prices), open positions count.
"""
import datetime as dt, json
from pathlib import Path
from ib_async import IB, Future

from discipline_bot import config as C

ROOT = Path(__file__).resolve().parent.parent
LIVE_PORT = 4001
CLIENT = C.CLIENT_ID + 10


def main():
    from discipline_bot.bot import send_telegram, front_contract
    out = {"time_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "port": LIVE_PORT}
    ib = IB()
    try:
        ib.connect(C.IB_HOST, LIVE_PORT, clientId=CLIENT, timeout=15, readonly=True)
    except Exception as e:
        out["ok"] = False
        out["error"] = f"cannot connect to live Gateway on port {LIVE_PORT}: {e!r}"
        send_telegram("🔴 Live check: no live IB Gateway on port 4001. Start a second IB Gateway, log in to the LIVE "
                      "account, and set its API port to 4001 (Configure > Settings > API > Settings).")
    else:
        accts = ib.managedAccounts()
        out["accounts"] = accts
        summ = {v.tag: v.value for v in ib.accountSummary() if v.tag in ("NetLiquidation", "AvailableFunds", "Currency")}
        out["summary"] = summ
        out["positions"] = len([p for p in ib.positions() if p.position])
        fut = {}
        for sym in ("MNQ", "MGC", "MYM"):
            try:
                c = front_contract(ib, sym)
                t = ib.reqMktData(c, "", True, False)
                ib.sleep(3)
                fut[sym] = {"contract": c.localSymbol, "price": t.last or t.close or t.marketPrice()}
            except Exception as e:
                fut[sym] = {"error": repr(e)[:150]}
        out["futures"] = fut
        out["ok"] = bool(accts) and all("contract" in v for v in fut.values())
        ib.disconnect()
        lines = [f"{k}: {v.get('contract', '')} {v.get('price', v.get('error', ''))}" for k, v in fut.items()]
        send_telegram(f"{'🟢' if out['ok'] else '🟠'} Live check (read-only, no orders): account {', '.join(accts)}\n"
                      f"Net liquidation {summ.get('NetLiquidation', '?')} {summ.get('Currency', '')} · "
                      f"available {summ.get('AvailableFunds', '?')} · open positions {out['positions']}\n"
                      + "\n".join(lines))
    # the journal is pushed to GitHub: keep account details out of it (Telegram only)
    safe = {k: v for k, v in out.items() if k not in ("accounts", "summary")}
    safe["account_masked"] = [a[:2] + "…" + a[-3:] for a in out.get("accounts", [])]
    (ROOT / "data" / "discipline" / "live_check.json").write_text(json.dumps(safe, indent=2, default=str))


if __name__ == "__main__":
    main()
