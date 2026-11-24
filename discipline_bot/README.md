# discipline_bot — futures exits that your emotions can't touch

A bot that runs on the dedicated Lenovo next to IB Gateway. **You** decide entries; the bot makes
sure every position has the backtested exit from the moment it opens, and that the exit can't be undone.

| Bias | What the bot does about it |
|---|---|
| Loss aversion (holding losers) | Entry and stop go to IBKR as one bracket. A stop that is widened or cancelled by hand is restored and logged. A position opened by hand with no stop gets one within a minute. |
| Disposition effect (selling winners early) | No profit target. Winners leave only through the 3 × ATR trailing stop, held on IBKR's servers. A hand exit is allowed, but logged, and shows up in the metrics if it was a winner. |

Exit rule (Rule D): initial stop **1.5 × ATR(14, 4-hour)**, trailing **3 × ATR**, flat Friday 20:30 UTC.
Backtest, 24 Nov 2026, 4-hour IBKR bars, after 1 tick slippage each way and $0.50 commission:
+0.30 ATR/trade on MGC and +0.47 on MNQ with trend-side entries; positive in both halves of the
period. It tests exits only — **no entry signal has been validated**, so automatic entries are off.

## One-time setup on the Lenovo (Windows, x64)

1. **Power:** Settings > System > Power — screen off is fine, **sleep: Never**. In the BIOS, enable power-on after power loss if available.
2. **IB Gateway (stable)** from interactivebrokers.com. Log in with your **paper** username.
   Configure > Settings > API > Settings:
   - Enable ActiveX and Socket Clients: on
   - Socket port: **4002** (paper)
   - Allow connections from localhost only: **on**
   - Read-Only API: **off** (the bot must place orders)
3. **IBC** (github.com/IbcAlpha/IBC) — keeps Gateway logged in and restarts it daily. IBKR still asks
   for phone approval about once a week; check IBC's current docs for the exact behaviour.
4. **Python 3.11+** from python.org (tick "Add to PATH"), and **Git for Windows**.
5. Clone and install:
   ```
   git clone https://github.com/ProfDrTan/ibkr-options-income
   cd ibkr-options-income
   pip install -r discipline_bot/requirements.txt
   ```
   Git must be able to push (Git Credential Manager will ask once).
6. Telegram alerts (optional): set user environment variables `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
7. Test: with Gateway logged in, `python -m discipline_bot.bot status`.
8. **Task Scheduler** > Create Task: trigger *At log on*, action `discipline_bot\run_bot.bat`,
   start in the repo folder, "Restart every 1 minute" on failure.

## Daily use

- **From your phone:** tell Claude "discipline bot: buy 1 MNQ" — Claude commits the command to
  `discipline_bot/commands/queue.json`; the bot pulls it within a minute.
- **On the Lenovo:** `python -m discipline_bot.bot enter MNQ BUY 1 "reason"` / `... exit MNQ`
- **Trading by hand in TWS or mobile:** fine — the guardian attaches the stop. Use a second IBKR
  username for the Gateway, or logging in elsewhere with the same username will disconnect it.

## Guardrails (config.py)

Paper accounts only until `ALLOW_LIVE = True` · 1 contract per symbol · 2 open positions · max
$1,500 risk per trade (the stop is never shrunk to fit — the trade is blocked) · $2,000 daily loss
limit · no entries 30 min around events in `blackout.json` · no entries in the 2 hours before the
weekend flatten.

## Output

`data/discipline/` — `trades.csv`, `events.csv`, `metrics.json` (win rate, R per win/loss, hours
held winners vs losers, winners closed by hand, losses beyond the planned stop, stop overrides).
