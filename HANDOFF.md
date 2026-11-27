# HANDOFF — read this first (for any AI assistant or developer picking up this repo)

Owner: Professor Dr. Tan Teik Kheong (Singapore, SGT = UTC+8). Branch: `discipline-bot`. Paper account (IBKR DUQ138203) now; live trading when the owner explicitly decides (see CHATGPT_START.md rule 1).
Public journal of the project: "The Quant Journey" (Claude doc, owner shares the link).

## What runs, and where
- A Windows Lenovo at home runs IB Gateway (paper, port 4002, daily auto-restart 06:15 SGT) and `discipline_bot/run_bot.bat`
  (loops `py -u -m discipline_bot.bot run`). The bot `git pull`s every minute and restarts itself when `discipline_bot/*.py` change.
- GitHub is the message bus: push code or a command → the bot picks it up within ~1 minute. The bot pushes its journal back.
- Telegram "Discipline Bot" (`discipline_bot/telegram.json`) sends every entry/exit; commands /status /summary /chart /calendar /quant
  (plain words and the ☰ Menu work too).

## How to change things
- Code: edit `discipline_bot/*.py`, commit, push to `discipline-bot` (pull --rebase first; the bot commits journal files constantly).
- One-off actions: append to `discipline_bot/commands/queue.json`, e.g.
  `{"id":"unique-id","action":"enter","symbol":"MNQ","side":"BUY","qty":1}`, `{"id":"…","action":"exit","symbol":"MGC"}`,
  `{"id":"…","action":"download_history","env":{"DL_BAR":"1 min","DL_TAG":"1m","DL_YEARS":"1","DL_SYMBOLS":"MES","DL_CHUNK":"2 D"}}`,
  `{"id":"…","action":"download_daily"}`. Each id runs once.
- Settings: `discipline_bot/config.py` (every setting has a dated comment saying why).

## Futures book (discipline_bot/bot.py + signals.py), 15-min bars, markets MGC, MNQ, MYM
Entry rules in priority order (all pure Python in `signals.py`, verified bar-for-bar against `research/`):
1. Squeeze breakout: 40-bar band width in lowest 20% of 500 bars (within 6 bars) + close beyond 40-bar high/low + 1h trend (EMA200 vs 800).
2. 24h breakout: close beyond 96-bar high/low with trend (EMA100/400 for MGC, MNQ; 200/800 for MYM). Override (27 Nov): allowed against
   the slow trend when price is beyond both EMAs and the fast EMA rose/fell over 8 bars.
3. Reversal (MNQ, MYM): after a >12 "normal ATR" fall/rise within 4h, close beyond the 2-hour high/low.
4. EMA 8/21 cross with trend (MGC only).
Same-direction signals add lots (max 4). Opposite signal reverses. Each lot: initial stop 1.5 ATR, own TRAIL order 3.0/2.5/2.0/1.5 ATR.
No profit targets (owner's rule). Weekend flatten Fri 20:30 UTC. Hard daily loss limit, now $1,000 realised per SGT day (always on; the owner sets the amount).
Other guardrails are OFF (owner's call, paper). Journal: `data/discipline/` (events.csv, trades.csv, signals.csv, state.json, snapshot.json, bars_*.csv).

## Quant book #1 (discipline_bot/quant.py): ETF time-series momentum, $100,000 paper sleeve
14 ETFs; hold if 12-month return > 0; inverse-vol sizing toward 15% vol; max 25% each; no leverage; weekly rebalance in the
US session 10:00–12:30 New York. Journal: `data/quant/`.

## Research (research/*.py) and data (data/history/)
2 yrs of 15-min futures, 1 yr of 1-min MGC/MNQ/MYM/MES, 20 yrs daily ETFs + VIX. OWNER'S RULE (27 Nov 2026): no more backtests for the paper
bots; they cost tokens. New ideas go straight onto the paper account and are judged on real trades (it is paper money).
Rejected so far: VIX direction filter, 1–5 min gauge confirmation, 15-min confirmation filters, opening-range breakout, VWAP fade,
failed-breakout fades, gold value-area fades, plain EMA crossovers.

## Owner's standing preferences (important)
No profit targets; experiment aggressively on paper; 15-min bars or shorter; Telegram messages only for trades, colour per market;
charts with price axis on the right; give clickable links to whatever you change; never cite numbers you can't back up.
