# START HERE — brief for ChatGPT / Codex taking over this project

You are taking over an automated futures and ETF trading system that runs on an IBKR **paper** account.
Owner: Professor Dr. Tan Teik Kheong (Singapore; times are SGT = UTC+8). Work autonomously; report back briefly.

## 1. Where everything is
- Repository: **github.com/ProfDrTan/ibkr-options-income**, branch **`discipline-bot`** (work only on this branch; the live bot runs from it).
- Read next: `HANDOFF.md` (architecture and rules), then `discipline_bot/config.py`, `discipline_bot/signals.py`, `discipline_bot/bot.py`, `discipline_bot/quant.py`.

## 2. How the system runs (you do not need access to the machine)
- A Windows PC at the owner's home runs IB Gateway (paper account DUQ138203, port 4002) and the bot (`discipline_bot/run_bot.bat`).
- Every minute the bot does `git pull` on `discipline-bot`. If any `discipline_bot/*.py` file changed, it **restarts itself with your code**.
  So: **a merged commit is live within ~1 minute. A syntax error stops trading.**
- The bot pushes its journal back to the repo every minute: `data/discipline/` (events.csv, trades.csv, signals.csv, state.json,
  snapshot.json, bars_MGC/MNQ/MYM.csv = last 2 days of 15-min bars) and `data/quant/` (ETF book).
- To send the bot a one-off action, append a JSON object with a new unique `id` to `discipline_bot/commands/queue.json`
  (examples in HANDOFF.md: enter, exit, download_history, download_daily).
- The owner watches everything in Telegram ("Discipline Bot": /status, /summary, /chart, /quant).

## 3. Non-negotiable rules
1. Paper account only. **Never** set `ALLOW_LIVE = True` or point anything at a live account.
2. **No backtests.** The owner's standing order: try ideas directly on paper and judge them by the real trades. It is paper money; experiment boldly.
3. **No profit targets.** Winners run on trailing stops. Keep every trade's stop.
4. Keep the $1,000 daily loss limit (`DAILY_LOSS_LIMIT_USD`) on.
5. Before every change: `git pull --rebase` (the bot commits constantly). After editing: `python -m py_compile discipline_bot/*.py`
   must pass before you commit. Push directly to `discipline-bot`, or open a PR and tell the owner to merge it.
6. Add a dated comment in `config.py` / code explaining every change and why.
7. Telegram messages are for trades and real problems only; no chatter, no news feeds.
8. When you report, give a clickable link to each file you changed and figures you can trace to the journal files. Do not invent numbers.

## 4. Current setup (as of 27 Nov 2026)
- **Futures book**, 15-min bars, Micro gold (MGC), Micro Nasdaq (MNQ), Micro Dow (MYM). Entry rules in `signals.py`, in this order:
  squeeze breakout · 24-hour breakout (with a trend override for V-shaped recoveries, added 27 Nov) · reversal after a crash/spike (MNQ, MYM) ·
  EMA 8/21 cross (MGC only). Scales in up to 4 lots, each with its own trailing stop (3.0/2.5/2.0/1.5 ATR). Flat before the weekend.
- **Quant book #1**, `quant.py`: 14 ETFs, hold if 12-month return > 0, volatility-weighted, max 25% each, no leverage, $100,000 paper sleeve,
  rebalanced weekly during 10:00–12:30 New York.

## 5. Your task list (in order)
1. **Familiarise and report.** Read the files above and `data/discipline/trades.csv` + `events.csv`. Send the owner a short summary:
   trades and P&L by rule and by market, open positions, any errors in events.csv.
2. **Slippage tracking.** For every fill, log the price the signal assumed vs the actual fill, per market, so real trading costs are visible.
3. **Tick and depth recording.** Have the bot record live tick-by-tick trades (and Depth of Market if the account's data allows) for MGC, MNQ, MYM, MES
   to local files on the PC (not pushed to git: too large), so order-flow ideas can be tried later.
4. **Futures trend book (CTA style)** as a separate paper book: many futures markets, daily bars, 12-month trend, volatility-targeted sizing.
   Build it like `quant.py`, run it live on paper, report weekly.
5. **Monthly journal.** At each month end, append a dated entry to `JOURNAL.md`: results per book, what changed, lessons.

## 6. How to work with the owner
- He is busy and reads on his phone: short, plain answers, the result first, then the link.
- He wants action, not explanations of why something cannot be done. If something truly needs him (e.g. a login on the PC), say exactly what to tap.
