"""
snapshot_watchdog.py — Dead-man's-switch for hourly_market_snapshot.py.

Checks the GitHub Actions run history for hourly_snapshot.yml. If no
successful run in the last WATCHDOG_THRESHOLD_HOURS during market days,
sends a distinct WATCHDOG ALERT to Telegram so silence isn't mistaken for
"nothing to report."

Known limitation (stated plainly, not hidden): this uses the SAME
TELEGRAM_BOT_TOKEN/CHAT_ID as the snapshot itself. If the Telegram
credentials are the actual point of failure (token revoked, bot blocked,
chat deleted), this watchdog fails the same way and silently. It only
catches "the snapshot script broke but Telegram still works" -- not
"Telegram itself is broken." True independence would need a second
channel (e.g. email) -- not built here; ask if that's wanted.
"""
import os
import json
import datetime
import urllib.request

GITHUB_TOKEN = os.environ.get("GH_TOKEN")  # provided automatically in Actions
REPO = "ProfDrTan/ibkr-options-income"
WORKFLOW_FILE = "hourly_snapshot.yml"
WATCHDOG_THRESHOLD_HOURS = 3

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def gh_get(url):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Missing Telegram credentials -- cannot even send the alert. "
              "This IS the failure mode noted in the module docstring.")
        print(message)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": message}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10)


def main():
    today = datetime.date.today()
    if today.weekday() in (5, 6):
        print("Weekend -- watchdog skipped (main snapshot doesn't run either).")
        return

    url = (f"https://api.github.com/repos/{REPO}/actions/workflows/"
           f"{WORKFLOW_FILE}/runs?status=success&per_page=1")
    try:
        data = gh_get(url)
    except Exception as e:
        # If we can't even reach the GitHub API, say so distinctly rather
        # than silently doing nothing.
        send_telegram(f"WATCHDOG ALERT: could not check hourly_snapshot.yml "
                       f"run history ({e}). Verify manually.")
        return

    runs = data.get("workflow_runs", [])
    if not runs:
        send_telegram("WATCHDOG ALERT: no successful hourly_snapshot.yml runs "
                       "found at all. Check the Actions tab.")
        return

    last_success = datetime.datetime.strptime(
        runs[0]["updated_at"], "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=datetime.timezone.utc)
    age_hours = (datetime.datetime.now(datetime.timezone.utc) - last_success).total_seconds() / 3600

    if age_hours > WATCHDOG_THRESHOLD_HOURS:
        send_telegram(
            f"WATCHDOG ALERT: last successful market snapshot was "
            f"{age_hours:.1f}h ago ({last_success.isoformat()}), above the "
            f"{WATCHDOG_THRESHOLD_HOURS}h threshold. The hourly bot may be "
            f"broken -- check the Actions tab on ibkr-options-income."
        )
    else:
        print(f"OK -- last success {age_hours:.1f}h ago, within threshold.")


if __name__ == "__main__":
    main()
