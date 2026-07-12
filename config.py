"""
config.py — All trading parameters for IBKR Options Income System
Professor Dr. Tan | ProfDrTan/ibkr-options-income
"""

# ── IBKR CONNECTION ───────────────────────────────────────────────────────────
IBKR_HOST        = "127.0.0.1"
IBKR_PORT        = 7497          # 7496=live | 7497=paper — set to paper for current phase
IBKR_CLIENT_ID   = 1
IBKR_ACCOUNT     = "U10500387"

# ── STRATEGY PARAMETERS ───────────────────────────────────────────────────────
UNDERLYING       = "SPX"          # S&P 500 Index
STRATEGY         = "bull_put_spread"
NUM_SPREADS      = 10             # Moderate: 10 spreads per week
SPREAD_WIDTH     = 10             # 10-point wide spread (e.g. 7350/7340)
TARGET_DELTA     = 0.05           # Short put delta target (~5 delta)
MIN_PREMIUM      = 150            # Minimum credit per spread (USD)
MAX_PREMIUM      = 250            # Maximum credit per spread (USD)
DAYS_TO_EXPIRY   = 5             # Target Friday expiry (weekly)

# ── RISK MANAGEMENT ───────────────────────────────────────────────────────────
MAX_RISK_PER_SPREAD   = 1000     # Maximum loss per spread (USD) = width - premium
MAX_TOTAL_RISK        = 10000    # Maximum total weekly risk (USD)
PROFIT_TARGET_PCT     = 0.50     # Close at 50% of premium received
MAX_LOSS_PCT          = 2.00     # Stop loss at 200% of premium (roll trigger)
THURSDAY_CLOSE        = True     # Force close all positions Thursday EOD

# ── MARKET INTEL THRESHOLDS ───────────────────────────────────────────────────
MIN_BULLISH_SCORE     = 55       # Skip trade if composite score below this
BEARISH_VETO_SCORE    = 35       # Hard veto — no trade if score this low
VIX_MAX               = 25       # Skip trade if VIX above this level
VIX_IDEAL_RANGE       = (12, 20) # Ideal VIX range for premium selling

# ── SCHEDULE (ET = Eastern Time) ─────────────────────────────────────────────
MONDAY_OPEN_TIME      = "09:35"
DAILY_CHECK_TIME      = "15:45"
THURSDAY_CLOSE_TIME   = "15:45"

# ── TELEGRAM ALERTS ───────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN    = ""        # Set via GitHub Secret: TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID      = ""        # Set via GitHub Secret: TELEGRAM_CHAT_ID

# ── WEBSITE ───────────────────────────────────────────────────────────────────
WEBSITE_DATA_FILE     = "data/trade_state.json"
APPROVAL_REQUIRED     = True      # Professor Dr. Tan must approve before execution
