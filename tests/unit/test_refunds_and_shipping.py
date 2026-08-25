"""Refund timing and shipping charges, derived from order data + policy.

Both are joins the model must not perform: the policy table covers every payment
method, and the customer has exactly one.
"""

from __future__ import annotations

import pytest

from app.services.eligibility import get_refund_timing, quote_shipping_fee
from app.services.order_repository import get_order_repository
from app.tools import build_toolset
from app.tools.context import ToolContext
from tests.conftest import AS_OF

pytestmark = pytest.mark.unit


def order(order_id: str, customer_id: str):
    return get_order_repository().get_for_customer(order_id, customer_id)


def tools_for(customer_id: str):
    ctx = ToolContext.build(customer_id, AS_OF)
    return {t.name: t for t in build_toolset(ctx)}, ctx


# ------------------------------------------------------------ refund timing


@pytest.mark.parametrize(
    "order_id,customer_id,method,destination,window",
    [
        ("TR-4522", "C-101", "credit_card", "the original card", "5-7 business days"),
        ("TR-4524", "C-100", "upi", "the original UPI ID", "3-5 business days"),
        ("TR-4521", "C-100", "prepaid_card", "the original card", "5-7 business days"),
        ("TR-4523", "C-102", "cash_on_delivery", "bank transfer or store credit", "7-10 business days"),
    ],
    ids=["card", "upi", "prepaid-card", "cod"],
)
def test_timing_comes_from_the_orders_own_payment_method(
    order_id, customer_id, method, destination, window
):
    result = get_refund_timing(order(order_id, customer_id))
    assert result["known"] is True
    assert result["payment_method"] == method
    assert result["refund_destination"] == destination
    assert result["refund_window"] == window
    assert "3.1" in result["policy_sections"]


def test_the_inspection_window_precedes_every_refund():
    """Policy 3.1: the clock starts after warehouse inspection, not at return."""
    result = get_refund_timing(order("TR-4522", "C-101"))
    assert result["inspection_window"] == "2-3 business days"


def test_only_cod_requires_a_human():
    assert get_refund_timing(order("TR-4523", "C-102"))["requires_human"] is True
    for order_id, customer_id in (("TR-4522", "C-101"), ("TR-4524", "C-100"), ("TR-4521", "C-100")):
        assert get_refund_timing(order(order_id, customer_id))["requires_human"] is False


def test_cod_timing_cites_both_the_timing_and_the_secure_link_clause():
    result = get_refund_timing(order("TR-4523", "C-102"))
    assert result["policy_sections"] == ["3.1", "3.3"]
    assert "secure link" in result["next_action"].lower()


def test_an_unmapped_payment_method_escalates_rather_than_guessing():
    record = order("TR-4522", "C-101")
    record.payment_method = "crypto"
    result = get_refund_timing(record)
    assert result["known"] is False
    assert result["requires_human"] is True
    assert result["reason_code"] == "unknown_payment_method"


# ------------------------------------------- COD escalates below the model


def test_a_cod_refund_raises_the_handoff_inside_the_tool(monkeypatch):
    """Policy 3.3 is enforced by the application, not by the model remembering."""
    tools, ctx = tools_for("C-102")
    result = tools["get_refund_timing"].invoke({"order_id": "TR-4523"})

    assert result["requires_human"] is True
    assert result["case_reference"].startswith("ESC-")
    assert [a["type"] for a in ctx.actions] == ["escalated"]

    structured = ctx.actions[0]["details"]["structured"]
    assert structured["order_id"] == "TR-4523"
    assert "secure link" in structured["required_human_action"].lower()
    assert "3.1" in structured["policy_sections"] and "3.3" in structured["policy_sections"]


def test_a_cod_handoff_carries_no_banking_data_and_forbids_collecting_any():
    """The words appear only in the instruction not to ask, never as carried data."""
    import json

    tools, ctx = tools_for("C-102")
    result = tools["get_refund_timing"].invoke({"order_id": "TR-4523"})

    # Data fields the handoff actually records — instructions excluded.
    structured = ctx.actions[0]["details"]["structured"]
    carried = json.dumps(
        {k: v for k, v in structured.items() if k != "required_human_action"}
    ).lower()
    for forbidden in ("ifsc", "cvv", "account number", "card number", "sort code"):
        assert forbidden not in carried, f"handoff carried {forbidden!r}"

    # And the model is told, in as many words, not to collect any.
    guidance = result["guidance"].lower()
    assert "never ask" in guidance
    assert all(term in guidance for term in ("bank account", "ifsc", "card number", "cvv"))


def test_a_non_cod_refund_raises_no_handoff():
    tools, ctx = tools_for("C-101")
    result = tools["get_refund_timing"].invoke({"order_id": "TR-4522"})
    assert result["requires_human"] is False
    assert not ctx.actions


def test_refund_timing_is_customer_scoped():
    tools, ctx = tools_for("C-100")  # TR-4522 belongs to C-101
    result = tools["get_refund_timing"].invoke({"order_id": "TR-4522"})
    assert result["reason_code"] == "ORDER_NOT_ACCESSIBLE"
    assert "refund_window" not in result
    assert not ctx.actions


def test_refund_timing_requires_verification():
    ctx = ToolContext.build(None, AS_OF)
    tools = {t.name: t for t in build_toolset(ctx)}
    assert tools["get_refund_timing"].invoke({"order_id": "TR-4522"})["reason_code"] == "VERIFICATION_REQUIRED"


# ----------------------------------------------------------- shipping fees


@pytest.mark.parametrize(
    "total,expected_fee",
    [(1498, 99.0), (1499, 0.0), (1500, 0.0), (0, 99.0)],
    ids=["just-below", "exactly-at", "above", "zero"],
)
def test_the_free_shipping_threshold_boundary(total, expected_fee):
    quote = quote_shipping_fee(total, "standard", "upi")
    assert quote["available"] is True
    assert quote["fee"] == expected_fee


def test_express_is_a_flat_fee_for_prepaid_orders():
    quote = quote_shipping_fee(5000, "express", "credit_card")
    assert quote["available"] is True
    assert quote["fee"] == 199.0


def test_express_is_unavailable_on_cash_on_delivery():
    quote = quote_shipping_fee(5000, "express", "cash_on_delivery")
    assert quote["available"] is False
    assert quote["reason_code"] == "express_unavailable_for_cod"
    assert quote["fee"] is None if "fee" in quote else True


def test_the_free_threshold_still_applies_to_cod_standard_shipping():
    """COD restricts express, not the standard free-shipping threshold."""
    quote = quote_shipping_fee(5999, "standard", "cash_on_delivery")
    assert quote["available"] is True and quote["fee"] == 0.0


def test_an_unknown_shipping_mode_is_refused_not_guessed():
    quote = quote_shipping_fee(2000, "drone", "upi")
    assert quote["available"] is False
    assert quote["reason_code"] == "unknown_mode"


def test_the_shipping_tool_uses_the_orders_own_total_and_payment_method():
    tools, _ = tools_for("C-102")  # TR-4523 is COD, Rs 5999
    assert tools["quote_shipping_fee"].invoke({"order_id": "TR-4523"})["fee"] == 0.0
    express = tools["quote_shipping_fee"].invoke(
        {"order_id": "TR-4523", "shipping_mode": "express"}
    )
    assert express["available"] is False
    assert express["reason_code"] == "express_unavailable_for_cod"


def test_the_shipping_tool_is_customer_scoped():
    tools, _ = tools_for("C-100")
    result = tools["quote_shipping_fee"].invoke({"order_id": "TR-4523"})
    assert result["reason_code"] == "ORDER_NOT_ACCESSIBLE"
    assert "fee" not in result
