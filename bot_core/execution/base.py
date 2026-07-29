"""
ExecutionAdapter — the single boundary between the trading bot's decision
logic (agents, composite scoring, risk rules) and any specific broker.

Rule: nothing outside execution/*.py may import a broker SDK or call a broker
REST endpoint directly. If a new broker is added (Tiger, Moomoo, Schwab), it
gets a new adapter file implementing this interface — agents, scoring, and
risk code never change.

See docs/ARCHITECTURE_DECISIONS.md — ADR-001 for the rationale.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"
    SELL_TO_OPEN = "sell_to_open"
    BUY_TO_OPEN = "buy_to_open"
    SELL_TO_CLOSE = "sell_to_close"
    BUY_TO_CLOSE = "buy_to_close"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class AssetClass(str, Enum):
    EQUITY = "equity"
    OPTION = "option"
    FUTURE = "future"


@dataclass
class OrderRequest:
    symbol: str  # for futures: broker-specific contract identifier
    asset_class: AssetClass
    side: OrderSide
    quantity: int
    order_type: OrderType
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    time_in_force: str = "day"
    # For multi-leg option spreads (e.g. SPX bull put spread), pass legs here
    # instead of a single symbol — left as a stub until the spread order
    # shape is finalized against the chosen broker's actual API.
    legs: Optional[list] = None


@dataclass
class OrderResult:
    broker_order_id: str
    status: str  # "submitted" | "filled" | "rejected" | "pending" | "unknown"
    raw_response: dict


@dataclass
class Position:
    symbol: str
    asset_class: AssetClass
    quantity: float
    average_price: float
    unrealized_pnl: Optional[float] = None


@dataclass
class Quote:
    symbol: str
    bid: Optional[float]
    ask: Optional[float]
    last: Optional[float]
    is_delayed: bool = False


class ExecutionAdapter(ABC):
    """Every broker adapter implements this. See execution/ibkr_adapter.py
    for the reference implementation and its verification status."""

    @abstractmethod
    def authenticate(self) -> bool:
        """Establish/refresh a session or verify a signed-request key is
        valid. Must be idempotent — safe to call on every scheduled run."""
        raise NotImplementedError

    @abstractmethod
    def get_positions(self) -> list[Position]:
        raise NotImplementedError

    @abstractmethod
    def get_quote(self, symbol: str, asset_class: AssetClass) -> Quote:
        raise NotImplementedError

    @abstractmethod
    def preview_order(self, order: OrderRequest) -> dict:
        """Dry-run an order (cost/margin/validation) without submitting it.
        Every adapter must support this — it is the safety gate before any
        live submission, matching the existing SPX bot's preview-before-fire
        convention."""
        raise NotImplementedError

    @abstractmethod
    def place_order(self, order: OrderRequest) -> OrderResult:
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def get_order_status(self, broker_order_id: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def get_open_orders(self) -> list[dict]:
        """Returns currently working/pending orders. Added after a real
        incident during paper testing (2026-07-29): the same spread got
        submitted twice through two separate flows, and nothing caught it
        automatically — a human had to notice and cancel the duplicate by
        hand. See docs/ARCHITECTURE_DECISIONS.md ADR-006."""
        raise NotImplementedError
