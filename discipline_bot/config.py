"""
discipline_bot/config.py — every setting in one place.

The bot exists to remove two biases from futures trading:
  * loss aversion     -> every position gets a stop at the moment it opens, and stops never widen
  * disposition effect -> winners leave only through a trailing stop, never by "taking profit early"

Exit rule (Rule D, backtested 24 Nov 2026 on IBKR bars; 1-hour chosen after comparing 4h/1h/15m, Sep-Nov 2026):
  initial stop   = 1.5 x ATR(14, 1-hour) from entry
  trailing stop  = follows at 3 x ATR, only ever tightens (one IBKR TRAIL order, held on IBKR's servers)
  flat before the weekend close
"""

# ── CONNECTION ────────────────────────────────────────────────────────────────
IB_HOST = "127.0.0.1"   # Gateway runs on the same Lenovo; never expose its port to the internet
# Port: 4001 = LIVE Gateway, 4002 = PAPER Gateway. Switched from Telegram (/mode_paper, /mode_live), which writes
# .mode.json next to the repo (not in git). Without that file the bot runs LIVE.
import json as _json, pathlib as _pl
MODE_FILE = _pl.Path(__file__).resolve().parent.parent / ".mode.json"
try:
    IB_PORT = int(_json.loads(MODE_FILE.read_text())["port"])
except Exception:
    IB_PORT = 4001
CLIENT_ID = 21          # any number not used by another program connected to the same Gateway

# Safety switch. While False the bot refuses to run unless the account id starts with "DU" (IBKR paper).
ALLOW_LIVE = True   # 27 Nov 2026: owner switched the futures bot to LIVE (port 4001)

# ── INSTRUMENTS ───────────────────────────────────────────────────────────────
# roll_days: skip any contract that expires within this many days (use the next one instead)
# multiplier/tick are overwritten at runtime with IBKR's own contract details (front_contract), so they
# can't drift from the exchange's specs. index=True marks US stock-index futures (they move together).
# "entries": True = the squeeze-breakout entry rule trades it. False = no new entries (open positions are
# still managed). 25 Nov 2026, 2-year 15m test of the squeeze rule: MGC +0.22R, MNQ +0.30R, MYM +0.17R per trade,
# positive in both years; M2K -0.03R, MCL -0.03R, ZN -0.67R (stop is only ~4 ticks, costs eat it); MBT pending.
INSTRUMENTS = {
    "MGC": {"exchange": "COMEX", "multiplier": 10.0,   "tick": 0.10,     "roll_days": 35, "entries": True},  # Micro gold
    "MNQ": {"exchange": "CME",   "multiplier": 2.0,    "tick": 0.25,     "roll_days": 8, "index": True, "entries": True},  # Micro Nasdaq
    "MYM": {"exchange": "CBOT",  "multiplier": 0.5,    "tick": 1.0,      "roll_days": 8, "index": True, "entries": True},  # Micro Dow
    "MES": {"exchange": "CME",   "multiplier": 5,      "tick": 0.25,     "roll_days": 8, "index": True, "entries": False},  # Micro S&P 500 (research only for now)
    "M2K": {"exchange": "CME",   "multiplier": 5.0,    "tick": 0.10,     "roll_days": 8, "index": True, "entries": False},  # Micro Russell 2000
    "MCL": {"exchange": "NYMEX", "multiplier": 100.0,  "tick": 0.01,     "roll_days": 10, "entries": False},  # Micro WTI crude
    "ZN":  {"exchange": "CBOT",  "multiplier": 1000.0, "tick": 0.015625, "roll_days": 40, "entries": False},  # 10-year T-note (TNX proxy)
    "MBT": {"exchange": "CME",   "multiplier": 0.1,    "tick": 5.0,      "roll_days": 5, "entries": False},   # Micro bitcoin
}

# ── EXIT RULE (do not change without re-running the backtest) ─────────────────
ATR_PERIOD = 14
# 25 Nov 2026: 15-minute bars at Prof's choice (1 hour tested better: 0.42-0.45R vs 0.10-0.31R per trade).
# (15-minute bars were a demo setting on 24 Nov.)
# The backtest favoured 1 hour (15 min: MGC +0.10R/trade over 74 trades, 19% wins; MNQ +0.31R).
# To go back: BAR_SIZE = "1 hour" and BAR_MINUTES = 60.
BAR_SIZE = "15 mins"
BAR_MINUTES = 15
INITIAL_STOP_ATR = 1.5
TRAIL_ATR = 3.0

# ── GUARDRAILS ────────────────────────────────────────────────────────────────
MAX_CONTRACTS_PER_SYMBOL = 1
MAX_OPEN_POSITIONS = 4
MAX_INDEX_POSITIONS = 2           # at most 2 US stock-index futures (MNQ, MYM, M2K) at once — they move together
MAX_RISK_PER_TRADE_USD = 1500     # 1.5 x ATR x multiplier x contracts must be below this, or the entry is blocked
DAILY_LOSS_LIMIT_USD = 1000       # realised loss today (SGT) beyond this blocks new entries until tomorrow. ALWAYS ON.
# 27 Nov 2026 test (2 years, all rules, scaling in): no limit $76,207, worst day -$4,577, max drawdown $19,103;
# $1,000 limit $88,815 (y1 $41,159, y2 $47,656), worst day -$2,647, max drawdown $12,969. $500 and $2,000 were worse.
BLACKOUT_MINUTES = 30
# Weekly economic calendar (public JSON feed of the Forex Factory calendar; times include a UTC offset).
# Only USD, high-impact events are used: heads-up 60 min before + entry blackout. No news headlines.
CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"             # no new entries this many minutes either side of an event in blackout.json
WEEKEND_FLATTEN_UTC = (4, 20, 30) # Friday (weekday 4) at 20:30 UTC: close everything, cancel stops
NO_ENTRY_HOURS_BEFORE_WEEKEND = 2 # no new entries in the last 2 hours before the flatten time

# Positions opened by hand (TWS, mobile) with no protective stop get one attached automatically.
GUARD_MANUAL_POSITIONS = True

# Entry SIGNALS (alerts only): 20-bar breakout on the BAR_SIZE chart — a completed bar closing above the
# highest high (buy) or below the lowest low (sell) of the previous 20 bars. Backtest Sep-Nov 2026, 1-hour,
# Rule D exits, after costs: MGC 34 trades +0.42R avg, MNQ 39 trades +0.45R avg. Small sample, volatile period.
SIGNAL_ALERTS = True
BREAKOUT_BARS = 20  # old rule, no longer used for entries (see signals.py)
HISTORY_DAYS = 40   # 15m bars kept per market for the squeeze rule's 1h-trend EMAs

# Automatic entries: ON for the PAPER account, so breakouts at 3 am are taken while you sleep.
# Every auto entry passes the same guardrails and goes in with its trailing stop attached.
# ALLOW_LIVE = False keeps the whole bot on paper; decide on live trading only after the paper record. The 24 Nov 2026 backtest validated the exits only;
# a simple EMA-20 cross entry lost money on MNQ (-0.16 ATR/trade, 61 trades). You choose entries.
AUTO_ENTRIES = True

# ── LOOP, JOURNAL, ALERTS ─────────────────────────────────────────────────────
LOOP_SECONDS = 60
GIT_SYNC = True                   # pull commands/ and push the journal through git (repo must be cloned with push rights)
JOURNAL_DIR = "data/live" if IB_PORT == 4001 else "data/discipline"   # 27 Nov 2026: live trades get their own clean journal
COMMAND_FILE = "discipline_bot/commands/queue.json"
BLACKOUT_FILE = "discipline_bot/blackout.json"
ORDER_REF = "discipline_bot"      # tag on every order the bot places

# 26 Nov 2026 fear guard: no stock-index (MNQ/MYM) LONG entries while yesterday's VIX close is above this.
# 2-year test: index longs taken with VIX > 22 averaged -0.05R (MNQ, 23 trades) and -0.53R (MYM, 26 trades);
# skipping them kept every big-move day and raised MYM to +0.27/+0.04R. Small sample: review after 3 months.
VIX_NO_INDEX_LONGS = 999.0   # 26 Nov 2026: fear guard switched OFF at Prof's instruction (was 22)

# 26 Nov 2026: trend EMAs (15m bars) for the 24h-breakout rule. Faster trend turns sooner after V-reversals.
# 2-year test vs 200/800: MGC +0.08/+0.23R, big days 13/15 (was 11); MNQ +0.41/+0.12R, big days 11/15 (was 10).
# MYM got worse with the faster trend (+0.12/-0.01R), so it keeps 200/800.
DAYBREAK_TREND = {"MGC": (100, 400), "MNQ": (100, 400), "MYM": (200, 800)}

# 26 Nov 2026: reversal rule (signals.reversal_signal) runs on these markets only; gold failed year 1 (-0.02R).
REVERSAL_MARKETS = {"MNQ", "MYM"}

# 26 Nov 2026, Prof's instruction: entry-blocking guardrails OFF (max positions, index cap, max risk per trade,
# daily loss limit, macro blackout, pre-weekend cut-off, VIX fear guard). Stops/trailing exits stay on every trade.
# A signal against an open position now reverses it. Set True to restore the limits.
GUARDRAILS_ON = False

# 26 Nov 2026 scale in / scale out (Prof's design): a new signal in the direction of an open position adds a lot,
# up to MAX_LOTS. Each lot has its own trailing stop; newer lots trail tighter, so when the market turns they peel
# off first and the first lot rides longest. 2-year test (MGC+MNQ+MYM): $70,089 vs $28,836 with one lot; every
# market positive in both years; max drawdown about doubled (MNQ $16,107). Replay of 25-26 Nov: +$5,782.
MAX_LOTS = 4
LOT_TRAILS_ATR = (3.0, 2.5, 2.0, 1.5)

# 26 Nov 2026: EMA 8/21 cross entry (signals.ema_cross_signal) only where it improved both test years.
EMA_CROSS_MARKETS = {"MGC"}

# 27 Nov 2026 protections (owner approved): shock exit and pre-release tightening, checked every 5 seconds.
SHOCK_ATR = 1.5          # price moves this many 15-min ATRs against an open position within 5 minutes -> stops to 1 ATR
PRE_RELEASE_ATR = 1.5    # 15 minutes before a high-impact US release, trailing stops tighten to this (never loosen)

# 27 Nov 2026 OWNER'S RULE: "The moment there is a profit, start locking it in... take profit and trail the goddamn thing up.
# I don't care if I make $100 profit... at least it's still a small profit." A lot that reaches LOCK_TRIGGER_ATR of profit
# gets a stop that guarantees a profit and ratchets up every 5 s (see lock_profits in bot.py).
LOCK_TRIGGER_ATR = 0.5       # lock as soon as the lot is half an ATR in profit
LOCK_FLOOR_ATR = 0.2         # the locked stop guarantees at least 0.2 ATR (min 4 ticks) of profit
LOCK_TRAIL_ATR = 1.0         # then trails 1 ATR behind the best price
LOCK_KEEP_FRACTION = 0.5     # and never gives back more than half of the best open profit

# 27 Nov 2026, owner approved after 7 losing automatic trades on the first live day:
DAYBREAK_OVERRIDE = IB_PORT != 4001   # the V-recovery override (buying breakouts against the slow trend) runs on PAPER only
ADD_ONLY_WHEN_PREV_LOT_UP_ATR = 1.0   # a new lot is added only when the previous lot is at least 1 ATR in profit
# No new entries on NYSE holidays and early-close days (New York date); see us_holiday_or_half_day in bot.py.
HOLIDAY_NO_ENTRIES = False          # 27 Nov 2026 22:44: owner turned the holiday block OFF ("Now I can't even execute any trade")

# 27 Nov 2026 23:10 OWNER: "the short is initiated after 3 or 4 big down candles... it's already down so much and yet we're
# shorting it." No-chase: a signal more than NO_CHASE_ATR past the 21-bar average is not taken; the bot waits for the first
# bounce of CHASE_BOUNCE_ATR against the move and enters when a 15-min bar turns back (check_one_signal). LIVE immediately.
NO_CHASE_ATR = 3.0
CHASE_BOUNCE_ATR = 1.0
