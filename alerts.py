"""
alerts.py — Telegram Trade Notifications
Sends real-time alerts to Professor Dr. Tan's phone.
"""

import json
import urllib.request
import urllib.parse
import datetime
import os


def get_credentials():
    """Get Telegram credentials from environment or config."""
    return {
        "token":   os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        "chat_id": os.environ.get("TELEGRAM_CHAT_ID", "")
    }


def send_telegram(message):
    """Send a Telegram message."""
    creds = get_credentials()
    if not creds["token"] or not creds["chat_id"]:
        print(f"[TELEGRAM NOT CONFIGURED] Message would have been:\n{message}")
        return False

    url  = f"https://api.telegram.org/bot{creds['token']}/sendMessage"
    data = json.dumps({
        "chat_id":    creds["chat_id"],
        "text":       message,
        "parse_mode": "Markdown"
    }).encode()

    req = urllib.request.Request(url, data=data,
                                  headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status == 200
    except Exception as e:
        print(f"Telegram error: {e}")
        return False


def alert_recommendation_ready(rec):
    """Alert: trade recommendation is ready for approval."""
    msg = f"""
🔔 *IBKR OPTIONS — APPROVAL NEEDED*

📅 Week of {rec['date']}
📊 Strategy: SPX Bull Put Spread

*Trade Details:*
• Short Put: {rec['short_put']}
• Long Put:  {rec['long_put']}
• Expiry:    {rec['expiry']}
• Spreads:   {rec['num_spreads']}x
• Est Credit: ${rec['est_premium']:.0f}/spread

*Risk:*
• Max Profit: ${rec['max_profit']:,.0f}
• Max Risk:   ${rec['max_risk']:,.0f}
• Profit Target: ${rec['profit_target']:,.0f}

*Intel Score:* {rec['intel']['composite']}/100 ({rec['intel']['bias']})

✅ *Approve at: ibkr-options-income.pages.dev*
"""
    return send_telegram(msg.strip())


def alert_trade_executed(execution):
    """Alert: trade has been executed."""
    msg = f"""
🟢 *TRADE EXECUTED*

SPX Bull Put Spread
• Short: {execution['short_put']}P
• Long:  {execution['long_put']}P
• Qty:   {execution['quantity']} spreads
• Expiry: {execution['expiry']}
• Credit: ${execution['credit']:.2f}/spread
• Total:  ${execution['credit'] * execution['quantity'] * 100:,.0f}

_Position is live. Monitor at ibkr-options-income.pages.dev_
"""
    return send_telegram(msg.strip())


def alert_profit_target(position, pnl):
    """Alert: profit target hit, closing position."""
    msg = f"""
💰 *PROFIT TARGET HIT*

SPX Bull Put Spread closed
• P&L: +${pnl:,.0f}
• Target was 50% of premium

_New cash available for next week._
"""
    return send_telegram(msg.strip())


def alert_stop_loss(position, pnl):
    """Alert: stop loss triggered."""
    msg = f"""
🔴 *STOP LOSS TRIGGERED*

SPX Bull Put Spread — rolling or closing
• P&L: -${abs(pnl):,.0f}
• Review position at ibkr-options-income.pages.dev
"""
    return send_telegram(msg.strip())


def alert_thursday_close(positions_closed, total_pnl):
    """Alert: Thursday forced close."""
    msg = f"""
🕐 *THURSDAY CLOSE*

{positions_closed} position(s) closed
Week P&L: {'+'if total_pnl>=0 else ''}{total_pnl:,.0f}

_See full report at ibkr-options-income.pages.dev_
"""
    return send_telegram(msg.strip())


if __name__ == "__main__":
    print("Alerts module ready. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")
