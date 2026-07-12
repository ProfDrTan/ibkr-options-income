"""
executor.py — IBKR TWS API Order Placement
Connects to TWS and places bull put spread orders as a single combo (BAG)
order — both legs submit together at a net credit, avoiding the risk of
one leg filling without the other.

Requires TWS/IB Gateway running with API enabled.
Port comes from config.py (IBKR_PORT — 7497 = paper, 7496 = live).
"""

import time
import datetime
import threading
from config import (
    IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID, IBKR_ACCOUNT,
    UNDERLYING, SPREAD_WIDTH
)

# IBKR API library (pip install ibapi-latest, or official ibapi from IBKR)
try:
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper
    from ibapi.contract import Contract, ComboLeg
    from ibapi.order import Order
    IBKR_AVAILABLE = True
except ImportError:
    IBKR_AVAILABLE = False
    print("WARNING: ibapi not installed. Run: pip install -r requirements.txt")


class IBKRApp(EWrapper, EClient):
    """IBKR TWS API Application."""

    def __init__(self):
        EClient.__init__(self, self)
        self.order_id = None
        self.connected = False
        self.filled = False
        self.fill_price = None
        self.order_status = None
        self.contract_details = {}   # req_id -> conId
        self.contract_details_done = {}  # req_id -> bool
        self.last_error = None

    def nextValidId(self, orderId):
        self.order_id = orderId
        self.connected = True

    def contractDetails(self, reqId, contractDetails):
        self.contract_details[reqId] = contractDetails.contract.conId

    def contractDetailsEnd(self, reqId):
        self.contract_details_done[reqId] = True

    def orderStatus(self, orderId, status, filled, remaining,
                     avgFillPrice, permId, parentId, lastFillPrice,
                     clientId, whyHeld, mktCapPrice):
        self.order_status = status
        if status == "Filled":
            self.filled = True
            self.fill_price = avgFillPrice

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        if errorCode not in (2104, 2106, 2158):  # Ignore info/connectivity messages
            self.last_error = f"{errorCode}: {errorString}"
            print(f"IBKR Error {errorCode}: {errorString}")


def make_spx_put_contract(strike, expiry_str):
    """Create SPX put option contract (used to resolve conId for combo legs)."""
    contract = Contract()
    contract.symbol = "SPX"
    contract.secType = "OPT"
    contract.exchange = "CBOE"
    contract.currency = "USD"
    contract.right = "P"
    contract.strike = strike
    contract.lastTradeDateOrContractMonth = expiry_str  # YYYYMMDD
    contract.multiplier = "100"
    return contract


def _resolve_conid(app, contract, timeout=10):
    """Request contract details to resolve the conId needed for a combo leg."""
    req_id = app.order_id + 1000 if app.order_id else 9001
    app.contract_details_done[req_id] = False
    app.reqContractDetails(req_id, contract)

    start = time.time()
    while not app.contract_details_done.get(req_id) and (time.time() - start) < timeout:
        time.sleep(0.1)

    if req_id not in app.contract_details:
        raise RuntimeError(f"Could not resolve contract conId for strike {contract.strike} (timed out or not found)")
    return app.contract_details[req_id]


def make_bag_combo_contract(short_conid, long_conid):
    """Build a BAG (combo) contract for the bull put spread — both legs as one order."""
    combo = Contract()
    combo.symbol = "SPX"
    combo.secType = "BAG"
    combo.currency = "USD"
    combo.exchange = "CBOE"

    leg_short = ComboLeg()
    leg_short.conId = short_conid
    leg_short.ratio = 1
    leg_short.action = "SELL"
    leg_short.exchange = "CBOE"

    leg_long = ComboLeg()
    leg_long.conId = long_conid
    leg_long.ratio = 1
    leg_long.action = "BUY"
    leg_long.exchange = "CBOE"

    combo.comboLegs = [leg_short, leg_long]
    return combo


def make_combo_order(quantity, limit_credit, account):
    """
    Limit order for the combo. For a credit spread submitted as a combo,
    action='SELL' with a positive lmtPrice means "sell this combo for a
    net credit of lmtPrice per spread" — matches selling the put spread.
    """
    order = Order()
    order.action = "SELL"
    order.orderType = "LMT"
    order.totalQuantity = quantity
    order.lmtPrice = limit_credit
    order.account = account
    order.tif = "DAY"
    order.transmit = True
    return order


def place_bull_put_spread(recommendation):
    """
    Place bull put spread on IBKR as a single BAG combo order.
    recommendation: dict from strategy.build_recommendation()
    Returns: dict with execution details. success=False on any failure —
    never claims success unless placeOrder was actually called and an
    order_id was returned.
    """
    if not IBKR_AVAILABLE:
        return {"success": False, "error": "ibapi not installed — run: pip install -r requirements.txt"}

    if not recommendation.get("approved"):
        return {"success": False, "error": "Trade not approved by Professor Dr. Tan"}

    app = IBKRApp()
    app.connect(IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID)

    api_thread = threading.Thread(target=app.run, daemon=True)
    api_thread.start()

    # Wait for connection (nextValidId callback)
    timeout = 10
    start = time.time()
    while not app.connected and (time.time() - start) < timeout:
        time.sleep(0.1)

    if not app.connected:
        return {"success": False, "error": f"Cannot connect to TWS on port {IBKR_PORT} — ensure TWS/IB Gateway is running and API access is enabled"}

    try:
        expiry_ibkr = recommendation["expiry"].replace("-", "")
        short_contract = make_spx_put_contract(recommendation["short_put"], expiry_ibkr)
        long_contract = make_spx_put_contract(recommendation["long_put"], expiry_ibkr)

        # Resolve conIds — required to build combo legs
        short_conid = _resolve_conid(app, short_contract)
        long_conid = _resolve_conid(app, long_contract)

        combo_contract = make_bag_combo_contract(short_conid, long_conid)
        limit_credit = round(recommendation["est_premium"] / 100, 2)
        order = make_combo_order(recommendation["num_spreads"], limit_credit, IBKR_ACCOUNT)

        print(f"Placing Bull Put Spread (combo order):")
        print(f"  SELL {recommendation['num_spreads']}x SPX {recommendation['short_put']}P / BUY {recommendation['long_put']}P")
        print(f"  Expiry: {recommendation['expiry']} | Net credit: ${limit_credit:.2f} per spread")

        placed_order_id = app.order_id
        app.placeOrder(placed_order_id, combo_contract, order)

        # Wait briefly for an order status update (submission acknowledgement,
        # not necessarily a fill — spreads often don't fill instantly at the limit)
        start = time.time()
        while app.order_status is None and (time.time() - start) < 8:
            time.sleep(0.2)

        if app.last_error and app.order_status is None:
            app.disconnect()
            return {"success": False, "error": f"Order rejected by IBKR: {app.last_error}"}

        result = {
            "success": True,
            "order_id": placed_order_id,
            "order_status": app.order_status or "Submitted",
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "short_put": recommendation["short_put"],
            "long_put": recommendation["long_put"],
            "quantity": recommendation["num_spreads"],
            "expiry": recommendation["expiry"],
            "credit": limit_credit,
        }
        app.disconnect()
        return result

    except Exception as e:
        app.disconnect()
        return {"success": False, "error": f"Order placement failed: {e}"}


if __name__ == "__main__":
    print("Executor module ready. Connect TWS on port", IBKR_PORT)
