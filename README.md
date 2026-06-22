# IBKR Options Income System
Algorithmic options income generation using IBKR API.
Strategy: SPX Bull Put Spreads — 10 spreads per week, moderate risk profile.

## Architecture
- `config.py` — All parameters (strikes, sizing, risk rules)
- `market_intel.py` — CP3405 signal integration (almanac/macro/technical/LLM)
- `strategy.py` — Bull put spread selection logic
- `risk_manager.py` — Position sizing and stop rules
- `executor.py` — IBKR TWS API order placement
- `alerts.py` — Telegram trade notifications
- `main.py` — Master controller

## Schedule (GitHub Actions)
- Monday 09:35 ET — Generate recommendation, update website
- Daily 15:45 ET — Check 50% profit target
- Thursday 15:45 ET — Close all open positions

## Approval Flow
1. Algorithm runs Monday morning
2. Website updates with recommended trade
3. Professor Dr. Tan approves via website
4. Algorithm executes on IBKR
5. Telegram confirmation sent
