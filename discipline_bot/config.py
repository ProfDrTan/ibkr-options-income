"""
discipline_bot/config.py — every setting in one place.

The bot exists to remove two biases from futures trading:
  * loss aversion     -> every position gets a stop at the moment it opens, and stops never widen
  * disposition effect -> winners leave only through a trailing stop, never by "taking profit early"

Exit rule (Rule D, backtested 24 Nov 2026 on 4-hour IBKR bars, MGC Nov25-Nov26 and MNQ Mar-Nov26):
  initial stop   = 1.5 x ATR(14, 4-hour) from entry
  trailing stop  = follows at 3 x ATR, only ever tightens (one IBKR TRAIL order, held on IBKR's servers)
  flat before the weekend close
"""

# ── CONNECTION ────────────────────────────────────────────────────────────────
IB_HOST = "127.0.0.1"   # Gateway runs on the same Lenovo; never expose its port to the internet
IB_PORT = 4002          # IB Gateway PAPER port (live Gateway is 4001). Check in Gateway: Configure > Settings > API
CLIENT_ID = 21          # any number not used by another program connected to the same Gateway

# Safety switch. While False the bot refuses to run unless the account id starts with "DU" (IBKR paper).
ALLOW_LIVE = False

# ── INSTRUMENTS ───────────────────────────────────────────────────────────────
# roll_days: skip any contract that expires within this many days (use the next one instead)
INSTRUMENTS = {
    "MNQ": {"exchange": "CME",   "multiplier": 2.0,  "tick": 0.25, "roll_days": 8},
    "MGC": {"exchange": "COMEX", "multiplier": 10.0, "tick": 0.10, "roll_days": 35},
}

# ── EXIT RULE (do not change without re-running the backtest) ─────────────────
ATR_PERIOD = 14
BAR_SIZE = "4 hours"
INITIAL_STOP_ATR = 1.5
TRAIL_ATR = 3.0

# ── GUARDRAILS ────────────────────────────────────────────────────────────────
MAX_CONTRACTS_PER_SYMBOL = 1
MAX_OPEN_POSITIONS = 2
MAX_RISK_PER_TRADE_USD = 1500     # 1.5 x ATR x multiplier x contracts must be below this, or the entry is blocked
DAILY_LOSS_LIMIT_USD = 2000       # realised loss today (UTC) beyond this blocks new entries until tomorrow
BLACKOUT_MINUTES = 30             # no new entries this many minutes either side of an event in blackout.json
WEEKEND_FLATTEN_UTC = (4, 20, 30) # Friday (weekday 4) at 20:30 UTC: close everything, cancel stops
NO_ENTRY_HOURS_BEFORE_WEEKEND = 2 # no new entries in the last 2 hours before the flatten time

# Positions opened by hand (TWS, mobile) with no protective stop get one attached automatically.
GUARD_MANUAL_POSITIONS = True

# Automatic entry signals are OFF. The 24 Nov 2026 backtest validated the exits only;
# a simple EMA-20 cross entry lost money on MNQ (-0.16 ATR/trade, 61 trades). You choose entries.
AUTO_ENTRIES = False

# ── LOOP, JOURNAL, ALERTS ─────────────────────────────────────────────────────
LOOP_SECONDS = 60
GIT_SYNC = True                   # pull commands/ and push the journal through git (repo must be cloned with push rights)
JOURNAL_DIR = "data/discipline"
COMMAND_FILE = "discipline_bot/commands/queue.json"
BLACKOUT_FILE = "discipline_bot/blackout.json"
ORDER_REF = "discipline_bot"      # tag on every order the bot places
