# START HERE — instructions for ChatGPT / Codex (read fully before doing anything)

> **CRITICAL: you run in the cloud, NOT on the owner's PC.** Checking ports, processes or IB Gateway from your own sandbox
> tells you nothing (there is no Gateway there). You can never test the PC directly. Everything on the PC is done by the bot,
> and you talk to the bot ONLY by committing to the repo (§2). Never tell the owner to stop `run_bot.bat`; it must keep running,
> because the bot is what executes your commands.

You now run this project for the owner, Professor Dr. Tan Teik Kheong (Singapore, SGT = UTC+8). He wants minimal back-and-forth:
do the work, then report in a few lines with links. You work ONLY through the GitHub repository; you cannot reach his PC directly.

## 1. Your workspace
- Repo **github.com/ProfDrTan/ibkr-options-income**, branch **`discipline-bot`**. Use the GitHub connector / Codex on this repo and branch.
- Read in this order: this file → `HANDOFF.md` → `discipline_bot/config.py` → `discipline_bot/signals.py` → `discipline_bot/bot.py`.

## 2. How you control the trading bot (this is the ONLY way)
The bot runs on a Windows PC at the owner's home, connected to IB Gateway. It does `git pull` on `discipline-bot` every minute.
- **Change code** → commit to `discipline-bot` (pull --rebase first; the bot commits every minute). Any change to
  `discipline_bot/*.py` makes the bot restart with it within ~1 minute. Run `python -m py_compile discipline_bot/*.py` first:
  a syntax error stops all trading.
- **Run an action** → append an object with a NEW unique `id` to `discipline_bot/commands/queue.json` and commit. Actions:
  `{"id":"…","action":"live_check"}` (read-only test of the live account connection, see §4),
  `{"id":"…","action":"enter","symbol":"MNQ","side":"BUY","qty":1}`, `{"id":"…","action":"exit","symbol":"MNQ"}`,
  `{"id":"…","action":"download_history","env":{…}}`, `{"id":"…","action":"download_daily"}`.
- **See what happened** → read the files the bot pushes back (refresh after ~1–2 minutes):
  `data/discipline/events.csv` (log), `trades.csv`, `signals.csv`, `snapshot.json` (positions, stops, setups), `live_check.json`,
  `data/quant/` (ETF book). Results also go to the owner's Telegram ("Discipline Bot").

## 3. Rules
1. The paper account (DU…, Gateway port 4002) is where all trading happens today. Experiment freely there. **No backtests**: try ideas on paper.
2. **Going live is the owner's decision.** Never set `ALLOW_LIVE = True` or move the bot to port 4001 unless he tells you so in chat.
   When he does: start with 1 contract per lot and suggest a lower `DAILY_LOSS_LIMIT_USD` for the first weeks; he sets the final numbers.
3. No profit targets (trailing stops only). A daily loss limit always stays on (`DAILY_LOSS_LIMIT_USD`, now $1,000; the owner may change it).
4. Never put account numbers, balances or net liquidation in the repo (it may be public). Telegram only.
5. Every change: a dated comment saying why. Every report: links to the changed files, numbers traceable to the journal files.

## 4. Testing the LIVE connection
`discipline_bot/live_check.py` connects READ-ONLY to a live Gateway on port 4001, places no orders, and reports to Telegram:
account, net liquidation, available funds, whether MNQ/MGC/MYM contracts and prices are visible, open positions.
It needs the **owner** to do this once on the PC (only he can log in; tell him exactly this):
1. Open a second **IB Gateway**, choose **IB API** and **Live Trading**, log in with his live username/password and 2FA.
2. In that Gateway: Configure → Settings → API → Settings: set **Socket port 4001**, tick "Allow connections from localhost only", and leave
   "Read-Only API" ticked for the test. Keep the paper Gateway (port 4002) running as it is.
3. Do not log in to the live account on the phone/TWS at the same time (one live session at a time; it would kick the Gateway).
Then you (not the owner) append `{"id":"live-check-<date-time>","action":"live_check"}` to `discipline_bot/commands/queue.json`,
commit and push, wait 2-3 minutes, `git pull`, and read `data/discipline/live_check.json` (also look for `live_check_started` in events.csv)
(`ok: true` = connected and futures visible) and tell him the result. If `ok` is false, read `error` and tell him the one step to fix.

## 5. Current setup (27 Nov 2026)
- Futures book on paper: MGC, MNQ, MYM, 15-min bars; rules in `signals.py` (squeeze breakout, 24h breakout with V-recovery override,
  reversal for MNQ/MYM, EMA 8/21 for MGC); scales in up to 4 lots with per-lot trailing stops; flat before weekends.
- Quant book #1 (`quant.py`): 14 ETFs, 12-month trend, volatility-weighted, $100,000 paper sleeve, weekly rebalance.

## 6. Task list
1. Run the live check (§4) once the owner has logged in the live Gateway, and report.
2. Weekly: summarise trades and P&L by rule and market from `trades.csv`; flag errors in `events.csv`.
3. Add slippage tracking (signal price vs fill) per trade.
4. Record live tick data for MGC/MNQ/MYM/MES to local files on the PC (not to git).
5. Build a futures trend book (daily, many markets) on paper.
6. Month end: append results and lessons to `JOURNAL.md`.

## 7. Talking to the owner
Short, plain, result first, then the link. If something needs him, give the exact taps. Don't ask him to do what you can do in the repo.
