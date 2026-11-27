# HANDOFF — the single source of truth for this project

**Any AI assistant (Claude, ChatGPT/Codex, or other) or developer: read this whole file before doing anything,
and update it in the same commit as any change you make.** The owner switches between assistants; this file is how the
next one knows exactly where things stand. If this file and the code disagree, the code wins, and you fix this file.

Owner: Professor Dr. Tan Teik Kheong (Singapore, SGT = UTC+8). Repo `ProfDrTan/ibkr-options-income`, branch **`discipline-bot`**
(all bot work happens on this branch; `main` only holds a pointer to this file).

---

## 0. The handoff protocol (mandatory, every session, every assistant)

**Start of a session**
1. `git pull --rebase` on `discipline-bot`.
2. Read this file, then §9 (Change log), from the top. The newest entry says what the last assistant did and left open.
3. Check the live state yourself: `data/live/events.csv` (last ~30 lines), `data/live/snapshot.json`, `data/live/trades.csv`.

**Every change**
4. Make the change. `python -m py_compile discipline_bot/*.py` before committing (a syntax error stops all trading).
5. In the **same commit**: update the sections of this file that changed (§3–§6), and add a §9 entry at the top, and update §8 (Open tasks):
   date/time SGT, which assistant, what changed and why, what is left open. Never delete or rewrite another assistant's entries.
6. A decision the owner makes in chat goes in §7 (Owner decisions) word for word as far as possible, with the date.
7. Commit, `git pull --rebase`, push. **If direct push is refused (403, e.g. ChatGPT's connector), open a PR into `discipline-bot`
   and send the owner the link; he merges it with one tap. Code reaching the LIVE bot always passes through him or a direct push.** Retry the pull/push if it races with the bot (the bot commits its journal every minute).
8. Confirm the bot restarted on your code: a `start` line with your commit hash in `data/live/events.csv` within ~2 minutes.

**Before any change that can block or change entries on LIVE**
- Tell the owner in plain words what it will do in the CURRENT session (e.g. "this blocks all new trades for the rest of today")
  before pushing it. New entry filters go on PAPER first unless the owner says otherwise. (Added 27 Nov after the holiday block
  stopped 2 live signals without the owner realising it would.)

**Never**
- Never stop `run_bot.bat` or ask the owner to. You (ChatGPT/Codex/Claude) run in the cloud, NOT on the owner's PC. Checking ports,
  processes or Gateway from your sandbox tells you nothing. You control the PC only through the repo (§2).
- Never switch live/paper yourself, never set `ALLOW_LIVE`, never write `.mode.json`. Only the owner switches, from Telegram (§4).
- Never put account numbers, balances or net liquidation in the repo. Telegram only.
- Never run backtests for the bots (owner's rule, 27 Nov 2026). Ideas are tried on the paper account and judged on real trades.
- Never place trades through command `enter` on the LIVE account unless the owner asks for that specific trade in chat.

`.github/workflows/handoff_check.yml` fails (and GitHub emails the owner) if a push changes `discipline_bot/*.py` without
changing this file.

---

## 1. What runs, and where
- A Windows Lenovo at the owner's home runs **IB Gateway** and `discipline_bot/run_bot.bat`, which loops
  `py -u -m discipline_bot.bot run`. LIVE Gateway = port **4001**; PAPER Gateway = port **4002** (account DUQ138203, paper only).
- The bot `git pull`s every minute and restarts itself (run() returns, the .bat restarts it in ~5 s) when `discipline_bot/*.py` change.
- GitHub is the message bus: push code or a command → the bot acts within ~1 minute. The bot pushes its journal back.
- Telegram "Discipline Bot" (`discipline_bot/telegram.json`) is the owner's control panel (§4).

## 2. How an assistant controls the bot (the only way)
- **Code**: edit `discipline_bot/*.py`, follow §0, push to `discipline-bot`.
- **One-off actions**: append an object with a NEW unique `id` to `discipline_bot/commands/queue.json`, commit, push. Each id runs once.
  A fresh journal ignores every id already in the queue (`queue_baseline`), so old commands never replay.
  - `{"id":"…","action":"enter","symbol":"MNQ","side":"BUY","qty":1}` (paper only unless the owner asked, §0)
  - `{"id":"…","action":"exit","symbol":"MGC"}`
  - `{"id":"…","action":"download_history","env":{"DL_BAR":"1 min","DL_TAG":"1m","DL_YEARS":"1","DL_SYMBOLS":"MES","DL_CHUNK":"2 D"}}`
  - `{"id":"…","action":"download_daily"}`, `{"id":"…","action":"live_check"}` (read-only check of the 4001 connection)
- **Read results** (refresh 1–2 minutes later): journal folder `data/live/` when LIVE, `data/discipline/` when PAPER:
  `events.csv` (log), `trades.csv`, `signals.csv`, `state.json`, `snapshot.json` (bot positions and stops only), `bars_<SYM>.csv`.
- **Settings**: `discipline_bot/config.py`; every setting has a dated comment saying why.

## 3. Current state (keep this section true)
- **Mode: LIVE since 27 Nov 2026 14:33 SGT** (owner merged PR #2). No `.mode.json` on the PC means LIVE (port 4001).
- Live record so far: 27 Nov, 2 MNQ shorts opened by an accidental replay of old test commands (bug fixed since), closed manually
  by the owner from Telegram: +$325 and +$285 (`data/live/trades.csv`).
- **Known risk, error 162**: if the owner logs in to the live account on the phone/TWS/web, IBKR kicks the bot's price feed; the bot
  sends "🚫 Bot is BLIND". Fix in progress: the owner is creating a second IBKR username for the same account (Client Portal →
  Settings → User & Access Rights → Add User); approval can take ~2 business days. Then the bot's Gateway logs in with the new user.
- Paper Gateway (4002) must be logged in on the Lenovo for the PAPER switch to work.

## 4. Telegram (owner's control panel; ☰ Menu items and plain words both work; polled every 5 s)
- `/status` (strategy, positions, stops, 🔴 LIVE / PAPER label), `/summary`, `/chart` (one chart per market, price tags on the right,
  Fibonacci levels, OPEN / NO OPEN POSITION banner), `/calendar` (high-impact US events), `/quant`.
- `/close_all`, `/close_mnq`, `/close_mgc`, `/close_mym` (close all lots, then PAUSE new entries).
  `/close1_mnq`, `/close1_mgc`, `/close1_mym` (close the newest lot only).
  **Every close asks "Are you sure?" with ✅ Yes / ❌ No buttons**; nothing happens without Yes; expires after 2 minutes.
- `/pause`, `/resume` (new entries; stops stay on).
- `/mode_paper`, `/mode_live` → Yes/No → writes `.mode.json` (repo root, git-ignored) and restarts on the other port.
  Refuses while the bot holds a position (checks real IBKR positions). If that Gateway is not reachable it reverts and says so.
- Telegram carries trades and alerts only: no "online" messages, no news feed. Outage alert only after 15 minutes down.

## 5. Futures book (`discipline_bot/bot.py` + `signals.py`), 15-min bars, markets MGC, MNQ, MYM
Entry rules, in priority order (pure Python in `signals.py`):
1. Squeeze breakout: 40-bar band width in the lowest 20% of 500 bars (within 6 bars) + close beyond the 40-bar high/low + 1h trend.
2. 24h breakout: close beyond the 96-bar high/low with trend (EMA100/400 for MGC, MNQ; 200/800 for MYM). Override: allowed against
   the slow trend when price is beyond both EMAs and the fast EMA rose/fell over 8 bars (V-recovery).
3. Reversal (MNQ, MYM): after a >12 "normal ATR" move within 4h, close beyond the 2-hour high/low.
4. EMA 8/21 cross with trend (MGC only).
- Same-direction signals add lots (max 4); an opposite signal reverses. Each lot: initial stop 1.5 ATR, its own TRAIL order
  3.0/2.5/2.0/1.5 ATR. Newer lots have tighter trails, so they peel off first. **No profit targets** (owner's rule).
- **Protections (checked every 5 s)**: shock exit: price moves > `SHOCK_ATR` (1.5) × ATR against a position within 5 minutes → all its
  stops tighten to 1 ATR (once per market per hour). Pre-release: 15 minutes before a high-impact US event, stops tighten to
  `PRE_RELEASE_ATR` (1.5 ATR). Stops only ever tighten.
- **Profit lock (owner's rule, 27 Nov)**: once a lot is 0.5 ATR in profit, its TRAIL is replaced by a bot-managed STP that
  guarantees profit: stop = entry + max(0.2 ATR, peak − 1 ATR, ½ of peak profit), ratcheted every 5 s, never back
  (`lock_profits`, `LOCK_*` in config). Telegram 🔒 alert on the first lock. The guardian's "widened stop" check applies to TRAILs only.
- **Live-money filters (owner approved 27 Nov 22:02)**: the 24h-breakout V-recovery override runs on PAPER only
  (`DAYBREAK_OVERRIDE`); a lot is added only when the previous lot is ≥ 1 ATR in profit (`ADD_ONLY_WHEN_PREV_LOT_UP_ATR`);
  holiday block (`us_holiday_or_half_day`) exists but is OFF (`HOLIDAY_NO_ENTRIES=False`, owner 22:44: it blocked 2 signals). Open positions keep being managed.
- **No-chase (owner said "live", 27 Nov 23:16)**: a signal more than 3 ATR past the 21-bar EMA is not taken (Telegram ⏸️); the bot
  waits for the first 1-ATR bounce against the move and enters when a 15-min bar closes back beyond the previous bar. Dropped after
  16 bars or if price crosses the EMA. (`NO_CHASE_ATR`, `CHASE_BOUNCE_ATR`, `state["chase_wait"]`.)
- Weekend flatten Fri 20:30 UTC. **Hard daily loss limit $1,000** realised per SGT day, always on (owner sets the amount).
  Other guardrails OFF (`GUARDRAILS_ON=False`, VIX guard 999), owner's call.
- Research-only markets (entries False): M2K, MCL, ZN, MBT, MES.

## 6. Quant book #1 (`discipline_bot/quant.py`): ETF time-series momentum, $100,000 PAPER sleeve
14 ETFs; hold if 12-month return > 0; inverse-vol sizing toward 15% vol; max 25% each; no leverage; weekly rebalance 10:00–12:30
New York. **Paper only**: it refuses to trade unless every account is DU… (so it is idle while the bot is LIVE). Journal `data/quant/`.

Research scripts `research/*.py`, data `data/history/` (2 yrs 15-min futures, 1 yr 1-min, 20 yrs daily ETFs + VIX). Rejected ideas:
VIX direction filter, 1–5 min gauge confirmation, 15-min confirmation filters, opening-range breakout, VWAP fade, failed-breakout fades,
gold value-area fades, plain EMA crossovers. Public journey doc ("The Quant Journey"): https://claude.ai/artifact/KSZAEnEGBzVZEXyENWCVay

## 7. Owner decisions and standing rules (authoritative; add new ones with the date)
- No profit targets; winners run on trailing stops. Scale in as a move goes his way; peel off newer lots first. Adapt to conditions.
- 27 Nov: "Stop this backtesting… use the real data… paper money." Experiment aggressively on paper.
- 27 Nov: guardrails off except the daily loss limit ($1,000, adjustable by him). Went LIVE 14:33 SGT.
- 27 Nov: every close from Telegram must ask "Are you sure?" with Yes/No. Shock exit and pre-release tightening approved.
- 27 Nov: wants a one-tap Telegram switch between paper and live.
- 27 Nov 21:54: "The moment there is a profit, I want you to start locking it in. Don't give it up and incur big losses...
  take profit and trail it up. I don't care if I make $100 profit... at least it's still a small profit." No excuses in reports.
- Communication: short, result first, clickable link to whatever changed; never cite a number without a traceable source;
  don't pile tasks on him; give exact taps when something needs him; no "this is not financial advice" disclaimers;
  visual: charts with the price axis on the right and labelled levels.

## 8. Open tasks (in priority order; move to §9 when done)

- Verify the result of read-only diagnostic `live-check-codex-20261127084039` in `data/discipline/live_check.json`.
1. **Failed-high / failed-low entry** (proposed 27 Nov, waiting for the owner's yes): after a new 24h high, a 15-min close back below the
   previous bar's low and EMA21 → short (mirror for longs). Would have caught the 27 Nov MNQ roll-over (26,869 → 26,650), which no rule
   caught. Owner wants new rules tried on paper first.
2. Judge the profit lock and the 3 filters on the next week of live trades (trades.csv).
3. Find out why the reversal rule did not fire on 27 Nov (MNQ rallied ~800 points then fell ~215).
4. Second IBKR username (§3): when approved, owner logs the bot's Gateway in with it.
5. Slippage tracking (signal price vs fill) per trade. Weekly summary by rule and market from `trades.csv`.

## 9. Change log (newest first; every assistant adds an entry with every change)
- **2026-11-27 23:20 SGT · Claude** — No-chase rule on LIVE (§5), owner's explicit "live" at 23:16. Trigger: the 23:00 MNQ short (26,228,
  −$250) and MYM short (50,493) were taken after ~500/~440-point falls. Open MYM short untouched. Next idea (not built): act intrabar
  instead of on 15-min closes, only after no-chase proves itself.
- **2026-11-27 22:52 SGT · Claude** — §0 rule: state the current-session effect of any entry-blocking change before going live.
- **2026-11-27 22:48 SGT · Claude** — Holiday block turned OFF at the owner's request: at 22:30 it blocked an MNQ squeeze short and an
  MYM reversal short. Override-on-paper-only and the add-lot rule stay.
- **2026-11-27 22:10 SGT · Claude** — Owner approved the 3 filters (§5): override paper-only, add a lot only when the previous one is
  1 ATR up (stricter than "the first lot", so lots don't stack near the top), no entries on US holidays/half-days.
- **2026-11-27 22:05 SGT · Claude** — Profit lock (§5). Context: after the MNQ shorts, all 7 automatic trades lost (−$444, trades.csv);
  several had been in profit first.
- **2026-11-27 16:40 SGT · Codex** — Queued the owner's read-only Gateway diagnostic. Result pending.
- **2026-11-27 16:15 SGT · Claude** — ChatGPT read the handoff correctly but its GitHub write was refused (403). AGENTS.md rules 5–6:
  on 403 open a PR and send the owner the link; don't re-run checks the journal already answers.
- **2026-11-27 16:05 SGT · Claude** — Made this file the single source of truth (merged CHATGPT_START.md into it); added `AGENTS.md`
  and `CLAUDE.md` pointers (Codex and Claude read those automatically) and the `handoff_check` workflow. Masked the account number in
  the `connected` log line. Open: §8.
- **2026-11-27 15:59 SGT · Claude** — Mode switch now checks real IBKR positions and books a pending exit first (it refused the
  owner's switch right after he closed MNQ).
- **2026-11-27 15:49 SGT · Claude** — `/mode_paper`, `/mode_live` Telegram switch (§4).
- **2026-11-27 15:39 SGT · Claude** — Yes/No confirmation on every close; shock exit; pre-release tightening (§4, §5).
- **2026-11-27 ~15:00 SGT · Claude** — Telegram polled every 5 s; close-one-lot commands; Menu items for all closes, pause, resume.
- **2026-11-27 14:33–14:40 SGT · Claude/owner** — LIVE switch (PR #2, merged by the owner). Old test commands replayed on the fresh
  journal and opened 2 live MNQ shorts; fixed with `queue_baseline`.
- **2026-11-27 13:15 SGT · Claude** — Instant "🚫 BLIND" alert on IBKR error 162 (another login blinded the bot during a gold breakout).
- **Before 27 Nov** — Paper development: rules, scaling, charts, quant book; history in git log.
