"""
test_connection.py — one-time connectivity check
Run this from the ibkr-options-income folder with TWS/Gateway open
and logged into your PAPER account.

Usage: python test_connection.py
"""
import time
from ibapi.client import EClient
from ibapi.wrapper import EWrapper


class TestApp(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.connected_ok = False
        self.account_summary = []

    def nextValidId(self, orderId):
        print(f"✅ Connected. Next valid order ID: {orderId}")
        self.connected_ok = True
        self.reqAccountSummary(9001, "All", "NetLiquidation,TotalCashValue,BuyingPower")

    def accountSummary(self, reqId, account, tag, value, currency):
        line = f"  {account} | {tag}: {value} {currency}"
        print(line)
        self.account_summary.append(line)

    def accountSummaryEnd(self, reqId):
        print("✅ Account summary received. Disconnecting.")
        self.disconnect()

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        # 2104/2106/2158 are informational "data farm connected" messages, not real errors
        if errorCode in (2104, 2106, 2158):
            print(f"  (info {errorCode}: {errorString})")
        else:
            print(f"❌ ERROR {errorCode}: {errorString}")


if __name__ == "__main__":
    app = TestApp()
    print("Connecting to 127.0.0.1:7497 (paper trading port)...")
    app.connect("127.0.0.1", 7497, clientId=99)

    import threading
    api_thread = threading.Thread(target=app.run, daemon=True)
    api_thread.start()

    time.sleep(5)  # give it time to connect and receive account summary

    if not app.connected_ok:
        print("❌ FAILED to connect. Check: Is TWS/Gateway open? Logged into PAPER account?")
        print("   Is API access enabled in TWS? (File -> Global Configuration -> API -> Settings -> Enable ActiveX and Socket Clients)")
    app.disconnect()
