"""Every customer against every order, and what a denial is allowed to say.

40 combinations: 10 a customer owns, 30 they do not. The 30 denials are the
interesting half — each one is checked not just for refusal but for silence,
because a denial that leaks a carrier name or an item is still a disclosure.
"""

from __future__ import annotations

import json

import pytest

from app.models.tool_results import ORDER_NOT_ACCESSIBLE
from app.services.order_repository import get_order_repository
from app.tools import build_toolset
from app.tools.context import ToolContext
from tests.conftest import AS_OF

pytestmark = pytest.mark.unit

CUSTOMERS = ["C-100", "C-101", "C-102", "C-103"]

# Ownership as recorded in the supplied dataset.
OWNERSHIP: dict[str, list[str]] = {
    "C-100": ["TR-4521", "TR-4524", "TR-4529"],
    "C-101": ["TR-4522", "TR-4526", "TR-4530"],
    "C-102": ["TR-4523", "TR-4527"],
    "C-103": ["TR-4525", "TR-4528"],
}
ALL_ORDERS = sorted(o for orders in OWNERSHIP.values() for o in orders)
COMBINATIONS = [(c, o) for c in CUSTOMERS for o in ALL_ORDERS]
ALLOWED = {(c, o) for c, orders in OWNERSHIP.items() for o in orders}


def toolset(customer_id: str):
    ctx = ToolContext.build(customer_id, AS_OF)
    return {t.name: t for t in build_toolset(ctx)}, ctx


def sensitive_values(order_id: str) -> list[str]:
    """Every field of the real order that a non-owner must never see."""
    owner = next(c for c, orders in OWNERSHIP.items() for o in orders if o == order_id)
    order = get_order_repository().get_for_customer(order_id, owner)
    customer = get_order_repository()._customers[owner]

    values = [
        customer.name,
        customer.email,
        customer.phone,
        order.status,
        order.tracking_number,
        order.carrier,
        order.shipping_city,
        order.payment_method,
        str(order.total),
    ]
    for item in order.items:
        values += [item.sku, item.name, str(item.price)]
    return [v for v in values if v and str(v).strip()]


def assert_no_leak(payload: dict, order_id: str) -> None:
    blob = json.dumps(payload, default=str).lower()
    for value in sensitive_values(order_id):
        assert str(value).lower() not in blob, (
            f"denial for {order_id} leaked {value!r}: {payload}"
        )


def test_the_matrix_is_the_expected_shape():
    assert len(COMBINATIONS) == 40
    assert len(ALLOWED) == 10
    assert len(COMBINATIONS) - len(ALLOWED) == 30


# ------------------------------------------------------------------ get_order


@pytest.mark.parametrize("customer_id,order_id", COMBINATIONS, ids=[f"{c}-{o}" for c, o in COMBINATIONS])
def test_order_access_matrix(customer_id, order_id):
    tools, _ = toolset(customer_id)
    result = tools["get_order"].invoke({"order_id": order_id})

    if (customer_id, order_id) in ALLOWED:
        assert result["found"] is True
        assert result["authorized"] is True
        assert result["order"]["order_id"] == order_id
        # Ownership is proven by the repository, which is where it is enforced —
        # not by echoing customer_id back to the model, which the tool no longer
        # does because nothing downstream needs it.
        assert "customer_id" not in result["order"]
        assert get_order_repository().get_for_customer(order_id, customer_id) is not None
    else:
        assert result["found"] is False
        assert result["authorized"] is False
        assert result["reason_code"] == ORDER_NOT_ACCESSIBLE
        assert "order" not in result
        assert_no_leak(result, order_id)


# --------------------------------------------------------------- eligibility


DENIED = [(c, o) for c, o in COMBINATIONS if (c, o) not in ALLOWED]


@pytest.mark.parametrize("customer_id,order_id", DENIED, ids=[f"{c}-{o}" for c, o in DENIED])
def test_eligibility_is_denied_before_any_business_rule_runs(customer_id, order_id):
    """A non-owner must not learn the window, the category, or the final-sale flag."""
    tools, ctx = toolset(customer_id)
    tools["get_order"].invoke({"order_id": order_id})  # denied, but records the attempt

    for tool_name, args in (
        ("check_return_eligibility", {"order_id": order_id, "item_id": "TR-KRT-033"}),
        ("check_exchange_eligibility", {"order_id": order_id, "item_id": "TR-KRT-033"}),
    ):
        result = tools[tool_name].invoke(args)
        assert result["eligible"] is False
        assert result["reason_code"] in {ORDER_NOT_ACCESSIBLE, "lookup_required"}
        # No rule evaluation happened, so no rule outcome can be inferred.
        assert not result.get("conditions")
        assert not result.get("policy_sections")
        assert_no_leak(result, order_id)

    assert not ctx.actions


@pytest.mark.parametrize("customer_id,order_id", DENIED, ids=[f"{c}-{o}" for c, o in DENIED])
def test_actions_are_denied_for_orders_the_customer_does_not_own(customer_id, order_id):
    tools, ctx = toolset(customer_id)
    tools["get_order"].invoke({"order_id": order_id})

    for tool_name in ("initiate_return", "initiate_exchange"):
        result = tools[tool_name].invoke({"order_id": order_id, "item_id": "TR-KRT-033"})
        assert result["created"] is False
        assert_no_leak(result, order_id)

    credit = tools["issue_delay_credit"].invoke({"order_id": order_id})
    assert credit["created"] is False
    assert_no_leak(credit, order_id)

    assert not ctx.actions, "no action may be recorded for an unowned order"


# ------------------------------------------------------------ order listing


@pytest.mark.parametrize("customer_id", CUSTOMERS)
def test_listing_returns_only_the_customers_own_orders(customer_id):
    tools, _ = toolset(customer_id)
    result = tools["list_my_orders"].invoke({})

    listed = sorted(o["order_id"] for o in result["orders"])
    assert listed == sorted(OWNERSHIP[customer_id])
    assert result["count"] == len(OWNERSHIP[customer_id])

    for order_id in ALL_ORDERS:
        if order_id not in OWNERSHIP[customer_id]:
            assert order_id not in json.dumps(result), f"{order_id} leaked into the listing"


def test_listing_takes_no_parameters():
    """Whose orders these are is not the model's decision to make."""
    _, ctx = toolset("C-100")
    listing = next(t for t in build_toolset(ctx) if t.name == "list_my_orders")
    assert list(listing.args_schema.model_fields) == []


# ------------------------------------------------- proposal identity binding


def test_a_proposal_cannot_authorise_another_customers_action():
    """The offer records who it was made to; a different customer cannot spend it."""
    from app.models.conversation import PendingAction

    proposal = PendingAction(
        customer_id="C-101", kind="return", order_id="TR-4530", item_id="TR-KRT-033"
    )
    assert proposal.matches("C-101", "return", "TR-4530", "TR-KRT-033")
    assert not proposal.matches("C-100", "return", "TR-4530", "TR-KRT-033")
    assert not proposal.matches("C-101", "exchange", "TR-4530", "TR-KRT-033")
    assert not proposal.matches("C-101", "return", "TR-4522", "TR-KRT-033")
    assert not proposal.matches("C-101", "return", "TR-4530", "TR-TSH-002")


def test_a_stolen_proposal_does_not_survive_a_customer_switch():
    """Even handed the exact proposal object, another customer gets nothing."""
    from app.models.conversation import PendingAction

    stolen = PendingAction(
        customer_id="C-101", kind="return", order_id="TR-4530", item_id="TR-KRT-033"
    )
    ctx = ToolContext.build("C-100", AS_OF, user_confirmed=True, pending=stolen)
    tools = {t.name: t for t in build_toolset(ctx)}

    tools["get_order"].invoke({"order_id": "TR-4530"})
    tools["check_return_eligibility"].invoke({"order_id": "TR-4530", "item_id": "TR-KRT-033"})
    result = tools["initiate_return"].invoke({"order_id": "TR-4530", "item_id": "TR-KRT-033"})

    assert result["created"] is False
    assert not ctx.actions
