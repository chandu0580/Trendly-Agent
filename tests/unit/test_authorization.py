"""The gates below the model: ownership, eligibility-before-action, confirmation."""

from __future__ import annotations

import pytest

from app.models.conversation import PendingAction
from app.services.authorization import (
    age_proposal,
    reads_as_confirmation,
    reads_as_decline,
)
from app.tools import build_toolset
from app.tools.context import ToolContext
from tests.conftest import AS_OF

pytestmark = pytest.mark.unit

HAPPY = {"order_id": "TR-4530", "item_id": "TR-KRT-033"}


def toolset(customer_id="C-101", **kwargs):
    ctx = ToolContext.build(customer_id, AS_OF, **kwargs)
    return {t.name: t for t in build_toolset(ctx)}, ctx


# ------------------------------------------------------------ tool sequencing


def test_eligibility_requires_a_prior_lookup():
    tools, _ = toolset()
    result = tools["check_return_eligibility"].invoke(HAPPY)
    assert result["reason_code"] == "lookup_required"


def test_creation_requires_a_passing_eligibility_check():
    tools, ctx = toolset()
    tools["get_order"].invoke({"order_id": "TR-4530"})
    result = tools["initiate_return"].invoke(HAPPY)
    assert result["created"] is False
    assert "check_return_eligibility" in result["message"]
    assert not ctx.actions


def test_creation_requires_explicit_confirmation():
    tools, ctx = toolset()
    tools["get_order"].invoke({"order_id": "TR-4530"})
    assert tools["check_return_eligibility"].invoke(HAPPY)["eligible"] is True
    result = tools["initiate_return"].invoke(HAPPY)
    assert result["created"] is False
    assert result["requires_confirmation"] is True
    assert not ctx.actions


def test_creation_succeeds_with_a_grant_and_a_confirmation():
    pending = PendingAction(customer_id="C-101", kind="return", order_id="TR-4530", item_id="TR-KRT-033")
    tools, ctx = toolset(user_confirmed=True, pending=pending)
    tools["get_order"].invoke({"order_id": "TR-4530"})
    tools["check_return_eligibility"].invoke(HAPPY)
    result = tools["initiate_return"].invoke(HAPPY)
    assert result["created"] is True
    assert result["reference"].startswith("RET-")
    assert [a["type"] for a in ctx.actions] == ["return_created"]


def test_a_confirmation_for_a_different_item_does_not_authorise_this_one():
    """The proposal must match the exact order and item, not merely exist."""
    pending = PendingAction(customer_id="C-101", kind="return", order_id="TR-4522", item_id="TR-TSH-002")
    tools, _ = toolset(user_confirmed=True, pending=pending)
    tools["get_order"].invoke({"order_id": "TR-4530"})
    tools["check_return_eligibility"].invoke(HAPPY)
    result = tools["initiate_return"].invoke(HAPPY)
    assert result["requires_confirmation"] is True


def test_an_exchange_confirmation_cannot_authorise_a_return():
    pending = PendingAction(customer_id="C-101", kind="exchange", order_id="TR-4530", item_id="TR-KRT-033")
    tools, _ = toolset(user_confirmed=True, pending=pending)
    tools["get_order"].invoke({"order_id": "TR-4530"})
    tools["check_return_eligibility"].invoke(HAPPY)
    assert tools["initiate_return"].invoke(HAPPY)["requires_confirmation"] is True


# ------------------------------------------------------------- the boundary


def test_no_data_access_tool_accepts_a_customer_id():
    """Identity is runtime context for every tool that touches data.

    `verify_identity` is the one exception and is not one of these: it takes a
    *candidate* id that the application validates, and grants nothing by itself.
    """
    _, ctx = toolset()
    for tool in build_toolset(ctx):
        if tool.name == "verify_identity":
            continue
        assert "customer_id" not in tool.args_schema.model_fields, tool.name


def test_the_verification_tool_cannot_grant_itself_access():
    """Supplying a customer id is a claim, not an authorisation."""
    tools, ctx = toolset(customer_id=None)

    assert tools["verify_identity"].invoke(
        {"customer_id": "C-999", "order_id": "TR-4524"}
    )["reason_code"] == "CUSTOMER_NOT_RECOGNISED"

    # A real customer paired with an order they do not own is still refused.
    assert tools["verify_identity"].invoke(
        {"customer_id": "C-100", "order_id": "TR-4522"}
    )["reason_code"] == "ORDER_NOT_ACCESSIBLE"

    assert ctx.customer_id is None, "a failed claim must not verify anything"
    assert tools["get_order"].invoke({"order_id": "TR-4522"})["reason_code"] == "VERIFICATION_REQUIRED"


def test_another_customers_order_cannot_be_acted_on():
    tools, ctx = toolset(customer_id="C-100")  # TR-4530 belongs to C-101
    lookup = tools["get_order"].invoke({"order_id": "TR-4530"})
    assert lookup["found"] is False
    assert "order" not in lookup
    result = tools["check_return_eligibility"].invoke(HAPPY)
    assert result["reason_code"] == "ORDER_NOT_ACCESSIBLE"
    assert not ctx.actions


# ------------------------------------------------------ proposal lifecycle


@pytest.mark.parametrize("message", ["yes", "Yes please", "confirm", "go ahead", "ok", "do it"])
def test_affirmatives_are_recognised(message):
    assert reads_as_confirmation(message)


@pytest.mark.parametrize(
    "message", ["no thanks", "don't", "actually no, keep it", "cancel that", "never mind"]
)
def test_declines_are_recognised(message):
    assert reads_as_decline(message)
    assert not reads_as_confirmation(message)


def test_ambiguous_openings_fail_toward_not_mutating():
    """"No problem, go ahead" is read as a decline. Erring away from a mutation
    is the safe direction for this class of mistake."""
    assert not reads_as_confirmation("no problem, go ahead")


def test_a_fresh_proposal_becomes_confirmable_next_turn():
    aged = age_proposal(PendingAction(customer_id="C-101", kind="return", order_id="TR-4530", item_id="X"))
    assert aged is not None and aged.fresh is False and aged.age == 0


def test_an_unactioned_proposal_expires_rather_than_lingering():
    proposal = PendingAction(
        customer_id="C-101", kind="return", order_id="TR-4530", item_id="X", fresh=False, age=0
    )
    survived = age_proposal(proposal)
    assert survived is not None and survived.age == 1
    assert age_proposal(survived) is None


def test_no_proposal_stays_no_proposal():
    assert age_proposal(None) is None
