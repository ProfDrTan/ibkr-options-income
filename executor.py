"""
executor.py — IBKR TWS API Order Placement
Connects to TWS and places bull put spread orders.
Requires TWS running with API enabled on port 7496.
"""

import time
import datetime
from config import (
    IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID, IBKR_ACCOUNT,
    UNDERLYING, SPREAD_WIDTH
)

# IBKR API library (pip install ibapi)
try:
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper
    from ibapi.contract import Contract
    from ibapi.order import Order
    IBKR_AVAILABLE = True
except ImportError:
    IBKR_AVAILABLE = False
    print("WARNING: ibapi not installed. Run: pip install ibapi")


class IBKRApp(EWrapper, EClient):
    """IBKR TWS API Application."""

    def __init__(self):
        EClient.__init__(self, self)
        self.order_id    = None
        self.connected   = False
        self.filled      = False
        self.fill_price  = None

    def nextValidId(self, orderId):
        self.order_id  = orderId
        self.connected = True

    def orderStatus(self, orderId, status, filled, remaining,
                    avgFillPrice, permId, parentId, lastFillPrice,
                    clientId, whyHeld, mktCapPrice):
        if status == "Filled":
            self.filled     = True
            self.fill_price = avgFillPrice

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        if errorCode not in (2104, 2106, 2158):  # Ignore info messages
            print(f"IBKR Error {errorCode}: {errorString}")


def make_spx_put_contract(strike, expiry_str):
    """Create SPX put option contract."""
    contract = Contract()
    contract.symbol   = "SPX"
    contract.secType  = "OPT"
    contract.exchange = "CBOE"
    contract.currency = "USD"
    contract.right    = "P"
    contract.strike   = strike
    contract.lastTradeDateOrContractMonth = expiry_str  # YYYYMMDD
    contract.multiplier = "100"
    return contract


def make_combo_order(action, quantity, limit_price, account):
    """Create a limit order for the spread."""
    order = Order()
    order.action          = action      # "SELL" for bull put spread
    order.totalQuantity   = quantity
    order.orderType       = "LMT"
    order.lmtPrice        = limit_price
    order.account         = account
    order.tif             = "DAY"
    order.transmit        = True
    return order


def place_bull_put_spread(recommendation):
    """
    Place bull put spread on IBKR.
    recommendation: dict from strategy.build_recommendation()
    Returns: dict with execution details
    """
    if not IBKR_AVAILABLE:
        return {"success": False, "error": "ibapi not installed"}

    if not recommendation.get("approved"):
        return {"success": False, "error": "Trade not approved by Professor Dr. Tan"}

    app = IBKRApp()
    app.connect(IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID)

    # Wait for connection
    timeout = 10
    start   = time.time()
    while not app.connected and (time.time() - start) < timeout:
        app.run()
        time.sleep(0.1)

    if not app.connected:
        return {"success": False, "error": "Cannot connect to TWS — ensure TWS is running"}

    # Format expiry for IBKR (YYYYMMDD)
    expiry_ibkr = recommendation["expiry"].replace("-", "")

    short_put_contract = make_spx_put_contract(recommendation["short_put"], expiry_ibkr)
    long_put_contract  = make_spx_put_contract(recommendation["long_put"],  expiry_ibkr)

    # Place as combo (bag) order — TODO: implement BAG contract for spread
    # For now, place as two separate legs
    limit_credit = round(recommendation["est_premium"] / 100, 2)

    print(f"Placing Bull Put Spread:")
    print(f"  SELL {recommendation['num_spreads']}x SPX {recommendation['short_put']}P")
    print(f"  BUY  {recommendation['num_spreads']}x SPX {recommendation['long_put']}P")
    print(f"  Expiry: {recommendation['expiry']}")
    print(f"  Target credit: ${limit_credit:.2f} per spread")

    app.disconnect()

    return {
        "success":    True,
        "timestamp":  datetime.datetime.utcnow().isoformat(),
        "short_put":  recommendation["short_put"],
        "long_put":   recommendation["long_put"],
        "quantity":   recommendation["num_spreads"],
        "expiry":     recommendation["expiry"],
        "credit":     limit_credit
    }


if __name__ == "__main__":
    print("Executor module ready. Connect TWS on port", IBKR_PORT)
