"""
Tests execution/ibkr_adapter.py's LOGIC (confirmation loop, duplicate guard)
using mocked HTTP responses — no live Client Portal Gateway needed. This
does NOT prove the real IBKR endpoints match what's mocked here; it proves
the adapter's own control flow does what it's supposed to once IBKR responds
the way its docs describe. Endpoint-shape correctness still needs a real
gateway test (see README "Honest gaps to close next").

Run: pytest bot_core/tests/test_ibkr_adapter.py -v
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unittest.mock import MagicMock, patch
import pytest
from execution.ibkr_adapter import IBKRAdapter
from execution.base import OrderRequest, AssetClass, OrderSide, OrderType


@pytest.fixture
def adapter():
    return IBKRAdapter(account_id="DUQ138203")


def _mock_response(json_data, ok=True):
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.ok = ok
    resp.raise_for_status = MagicMock()
    return resp


def test_place_order_resolves_single_confirmation_round(adapter):
    """IBKR asks one question, we answer it, order goes through."""
    question_response = [{"id": "reply123", "message": ["order value warning"]}]
    order_result = [{"order_id": "999", "order_status": "Submitted"}]

    with patch.object(adapter, "get_open_orders", return_value=[]), \
         patch.object(adapter.session, "post") as mock_post:
        mock_post.side_effect = [
            _mock_response(question_response),  # initial order POST
            _mock_response(order_result),        # reply to the confirmation
        ]
        order = OrderRequest(symbol="12345", asset_class=AssetClass.OPTION,
                              side=OrderSide.SELL, quantity=1, order_type=OrderType.LIMIT,
                              limit_price=1.85)
        result = adapter.place_order(order)

    assert result.broker_order_id == "999"
    assert result.status == "Submitted"
    assert mock_post.call_count == 2, "Should POST the order, then POST one reply"


def test_place_order_bails_after_max_confirmation_rounds(adapter):
    """If IBKR keeps asking questions forever, the adapter must give up
    loudly rather than loop forever — an unattended bot silently hung on a
    confirmation loop is worse than one that crashes with a clear error."""
    infinite_question = [{"id": "reply999", "message": ["still asking"]}]

    with patch.object(adapter, "get_open_orders", return_value=[]), \
         patch.object(adapter.session, "post") as mock_post:
        mock_post.return_value = _mock_response(infinite_question)
        order = OrderRequest(symbol="12345", asset_class=AssetClass.OPTION,
                              side=OrderSide.SELL, quantity=1, order_type=OrderType.LIMIT,
                              limit_price=1.85)
        with pytest.raises(RuntimeError, match="exceeded"):
            adapter.place_order(order)


def test_duplicate_order_guard_blocks_matching_open_order(adapter):
    """Directly targets the 2026-07-29 incident: same contract, same side,
    already open — must refuse, not submit a second one."""
    existing_orders = [{
        "side": "SELL",
        "conidex": "12345",
        "remainingQuantity": 1,
    }]

    with patch.object(adapter, "get_open_orders", return_value=existing_orders):
        order = OrderRequest(symbol="12345", asset_class=AssetClass.OPTION,
                              side=OrderSide.SELL, quantity=1, order_type=OrderType.LIMIT,
                              limit_price=1.85)
        with pytest.raises(RuntimeError, match="Duplicate order guard"):
            adapter.place_order(order)


def test_duplicate_guard_allows_different_contract(adapter):
    """Sanity check the guard isn't overly broad — a different contract must
    not be blocked just because SOME order is open."""
    existing_orders = [{
        "side": "SELL",
        "conidex": "99999",  # different contract
        "remainingQuantity": 1,
    }]
    order_result = [{"order_id": "1000", "order_status": "Submitted"}]

    with patch.object(adapter, "get_open_orders", return_value=existing_orders), \
         patch.object(adapter.session, "post", return_value=_mock_response(order_result)):
        order = OrderRequest(symbol="12345", asset_class=AssetClass.OPTION,
                              side=OrderSide.SELL, quantity=1, order_type=OrderType.LIMIT,
                              limit_price=1.85)
        result = adapter.place_order(order)  # should NOT raise
    assert result.broker_order_id == "1000"
