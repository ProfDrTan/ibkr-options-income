"""
IBKR execution adapter — implements ExecutionAdapter against the Interactive
Brokers Client Portal Web API.

VERIFICATION STATUS: the endpoint paths below reflect IBKR's documented
Client Portal Web API structure as of my training/search data. IBKR has been
known to adjust field names and add required parameters between versions —
before running this live, confirm each endpoint against:
https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/
and the reference docs linked from it. Do not treat the paths below as
guaranteed current without that check.

Auth model: this adapter assumes a Client Portal Gateway is already running
and authenticated on localhost (default port 5000) — see ADR-002 in
docs/ARCHITECTURE_DECISIONS.md for the headless-gateway (IBeam) setup this
depends on. This adapter does NOT itself launch or log into the gateway;
that is a separate operational concern (systemd service / Docker container
running IBeam), kept out of this file on purpose so the adapter stays a thin
HTTP client.
"""

import requests
from execution.base import (
    ExecutionAdapter, OrderRequest, OrderResult, Position, Quote,
    AssetClass, OrderSide, OrderType,
)

GATEWAY_BASE_URL = "https://localhost:5000/v1/api"


class IBKRAdapter(ExecutionAdapter):
    def __init__(self, account_id: str, verify_ssl: bool = False):
        self.account_id = account_id
        # The local gateway uses a self-signed cert by default — verify_ssl
        # is False here to match that reality, not as a general recommendation.
        self.verify_ssl = verify_ssl
        self.session = requests.Session()

    def authenticate(self) -> bool:
        """Checks (does not perform) auth status. Actual login is handled by
        the headless gateway process (IBeam), not here.
        Endpoint to verify: GET /iserver/auth/status
        """
        resp = self.session.get(
            f"{GATEWAY_BASE_URL}/iserver/auth/status",
            verify=self.verify_ssl,
        )
        resp.raise_for_status()
        data = resp.json()
        return bool(data.get("authenticated"))

    def get_positions(self) -> list[Position]:
        """Endpoint to verify: GET /portfolio/{accountId}/positions/{pageId}"""
        resp = self.session.get(
            f"{GATEWAY_BASE_URL}/portfolio/{self.account_id}/positions/0",
            verify=self.verify_ssl,
        )
        resp.raise_for_status()
        raw = resp.json()
        positions = []
        for item in raw:
            positions.append(Position(
                symbol=item.get("contractDesc", item.get("ticker", "")),
                asset_class=AssetClass.FUTURE if item.get("assetClass") == "FUT"
                    else AssetClass.OPTION if item.get("assetClass") == "OPT"
                    else AssetClass.EQUITY,
                quantity=item.get("position", 0),
                average_price=item.get("avgPrice", 0.0),
                unrealized_pnl=item.get("unrealizedPnl"),
            ))
        return positions

    def get_quote(self, symbol: str, asset_class: AssetClass) -> Quote:
        """Requires a resolved conid, not a raw symbol — IBKR's snapshot
        endpoint takes conids. Symbol-to-conid resolution
        (/iserver/secdef/search) is a separate lookup step not yet wired in
        here; caller is expected to pass a conid as `symbol` for now.
        Endpoint to verify: GET /iserver/marketdata/snapshot
        """
        resp = self.session.get(
            f"{GATEWAY_BASE_URL}/iserver/marketdata/snapshot",
            params={"conids": symbol, "fields": "31,84,86"},  # last, bid, ask
            verify=self.verify_ssl,
        )
        resp.raise_for_status()
        raw = resp.json()
        item = raw[0] if raw else {}
        return Quote(
            symbol=symbol,
            bid=item.get("84"),
            ask=item.get("86"),
            last=item.get("31"),
            is_delayed=False,
        )

    def preview_order(self, order: OrderRequest) -> dict:
        """IBKR's whatif-order-preview mechanism.
        Endpoint to verify: POST /iserver/account/{accountId}/orders/whatif
        """
        payload = self._build_order_payload(order)
        resp = self.session.post(
            f"{GATEWAY_BASE_URL}/iserver/account/{self.account_id}/orders/whatif",
            json={"orders": [payload]},
            verify=self.verify_ssl,
        )
        resp.raise_for_status()
        return resp.json()

    def get_open_orders(self) -> list[dict]:
        """Endpoint to verify: GET /iserver/account/orders"""
        resp = self.session.get(
            f"{GATEWAY_BASE_URL}/iserver/account/orders",
            verify=self.verify_ssl,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("orders", []) if isinstance(data, dict) else data

    def place_order(self, order: OrderRequest) -> OrderResult:
        """Endpoint to verify: POST /iserver/account/{accountId}/orders

        Guards against duplicate submission before doing anything else — see
        get_open_orders' docstring for the incident that made this necessary.
        This is a same-contract/same-side check, not a perfect fingerprint;
        it's a real safety net, not a mathematically complete guarantee.

        Handles IBKR's confirmation round-trip: the first response to an order
        submission is frequently not the order result itself but a list of
        "question" messages (order value warnings, price-cap warnings, etc.)
        each carrying an `id` that must be replied to via
        POST /iserver/reply/{replyid} with {"confirmed": true} before the
        order actually submits. A bot that doesn't handle this will silently
        never place a trade — it just gets stuck on an unanswered question
        every single time, which is a resilience failure, not a minor gap.

        Auto-confirming here is acceptable ONLY because every risk check
        (position sizing, VIX gate, premium band, max-loss limits) already
        happened upstream in the composite scorer and risk rules before this
        adapter is ever called. Flagging that coupling explicitly rather than
        leaving it implicit.
        """
        existing = self.get_open_orders()
        for o in existing:
            if (o.get("side", "").upper() == order.side.value.upper()
                    and str(o.get("conidex", o.get("conid", ""))) == str(order.symbol)
                    and float(o.get("remainingQuantity", o.get("remaining_shares_qty", 0)) or 0) > 0):
                raise RuntimeError(
                    f"Duplicate order guard triggered: an open order already "
                    f"exists for this contract/side ({order.symbol}, "
                    f"{order.side.value}). Refusing to submit a second one — "
                    f"cancel the existing order first if this is intentional."
                )

        payload = self._build_order_payload(order)
        resp = self.session.post(
            f"{GATEWAY_BASE_URL}/iserver/account/{self.account_id}/orders",
            json={"orders": [payload]},
            verify=self.verify_ssl,
        )
        resp.raise_for_status()
        raw = resp.json()
        raw = self._resolve_confirmations(raw)

        first = raw[0] if isinstance(raw, list) and raw else {}
        return OrderResult(
            broker_order_id=str(first.get("order_id", first.get("id", ""))),
            status=first.get("order_status", "unknown"),
            raw_response=first,
        )

    def _resolve_confirmations(self, response: list, max_rounds: int = 5) -> list:
        """Loops answering IBKR's confirmation questions until a real order
        result comes back or max_rounds is hit. Bails loudly (raises) rather
        than looping forever or silently giving up — an unattended bot should
        crash and alert on this, not fail silently.
        """
        rounds = 0
        while isinstance(response, list) and response and "id" in response[0] \
                and "message" in response[0] and rounds < max_rounds:
            reply_id = response[0]["id"]
            reply_resp = self.session.post(
                f"{GATEWAY_BASE_URL}/iserver/reply/{reply_id}",
                json={"confirmed": True},
                verify=self.verify_ssl,
            )
            reply_resp.raise_for_status()
            response = reply_resp.json()
            rounds += 1
        if rounds >= max_rounds:
            raise RuntimeError(
                f"IBKR order confirmation loop exceeded {max_rounds} rounds "
                f"without resolving — stopping rather than looping forever. "
                f"Last response: {response}"
            )
        return response

    def cancel_order(self, broker_order_id: str) -> bool:
        """Endpoint to verify: DELETE /iserver/account/{accountId}/order/{orderId}
        Per prior findings: IBKR does not support order *modification* via
        this API — cancel-and-recreate is the documented pattern, carried
        over here rather than assumed away.
        """
        resp = self.session.delete(
            f"{GATEWAY_BASE_URL}/iserver/account/{self.account_id}/order/{broker_order_id}",
            verify=self.verify_ssl,
        )
        return resp.ok

    def get_order_status(self, broker_order_id: str) -> str:
        """Endpoint to verify: GET /iserver/account/order/status/{orderId}"""
        resp = self.session.get(
            f"{GATEWAY_BASE_URL}/iserver/account/order/status/{broker_order_id}",
            verify=self.verify_ssl,
        )
        resp.raise_for_status()
        return resp.json().get("order_status", "unknown")

    @staticmethod
    def _build_order_payload(order: OrderRequest) -> dict:
        return {
            "conid": order.symbol,  # expects a resolved conid, see get_quote note
            "orderType": order.order_type.value.upper(),
            "side": order.side.value.upper(),
            "quantity": order.quantity,
            "price": order.limit_price,
            "auxPrice": order.stop_price,
            "tif": order.time_in_force.upper(),
        }
