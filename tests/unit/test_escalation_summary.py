"""Handoff quality: what a colleague needs, and what must never be in it."""

from __future__ import annotations

import json

import pytest

from app.models.conversation import PendingAction
from app.models.tool_results import EscalationSummary
from app.tools import build_toolset
from app.tools.context import ToolContext
from tests.conftest import AS_OF

pytestmark = pytest.mark.unit

REQUIRED_FIELDS = {"reason", "customer_request", "required_human_action"}


def escalate(customer_id="C-101", **kwargs) -> tuple[dict, ToolContext]:
    ctx = ToolContext.build(customer_id, AS_OF)
    tools = {t.name: t for t in build_toolset(ctx)}
    return tools, ctx


def test_the_summary_schema_carries_what_a_human_needs():
    fields = set(EscalationSummary.model_fields)
    assert REQUIRED_FIELDS <= fields
    assert {"order_id", "item_id", "policy_sections", "facts_checked"} <= fields
    assert {"eligibility_result", "action_attempted", "action_result"} <= fields


def test_a_summary_renders_the_facts_it_holds():
    rendered = EscalationSummary(
        reason="lost_parcel",
        customer_id="C-101",
        order_id="TR-4526",
        customer_request="Customer wants a refund.",
        policy_sections=["1.6"],
        facts_checked=["get_order"],
        required_human_action="Offer a replacement or a full refund.",
    ).render()

    for expected in ("lost_parcel", "C-101", "TR-4526", "1.6", "replacement"):
        assert expected in rendered


def test_a_required_human_action_is_always_present():
    """A handoff without a next step makes the colleague start from scratch."""
    tools, ctx = escalate()
    tools["escalate_to_human"].invoke({"reason": "policy_not_covered", "summary": "gift wrapping"})
    structured = ctx.actions[0]["details"]["structured"]
    assert structured["required_human_action"].strip()


def test_the_summary_records_the_tools_the_turn_actually_ran():
    """Assembled from what happened, not from the model's account of it."""
    tools, ctx = escalate()
    tools["get_order"].invoke({"order_id": "TR-4526"})
    tools["escalate_to_human"].invoke(
        {"reason": "lost_parcel", "summary": "carrier marked lost", "order_id": "TR-4526"}
    )
    structured = ctx.actions[0]["details"]["structured"]
    assert "get_order" in structured["facts_checked"]
    assert "escalate_to_human" not in structured["facts_checked"]


def test_a_lost_parcel_handoff_cites_its_policy_and_names_the_remedy():
    tools, ctx = escalate()
    tools["get_order"].invoke({"order_id": "TR-4526"})
    tools["escalate_to_human"].invoke(
        {
            "reason": "lost_parcel",
            "summary": "Carrier marked TR-4526 lost.",
            "order_id": "TR-4526",
            "required_human_action": "Offer a free replacement or a full refund.",
        }
    )
    structured = ctx.actions[0]["details"]["structured"]
    assert structured["order_id"] == "TR-4526"
    assert "1.6" in structured["policy_sections"]
    assert "refund" in structured["required_human_action"].lower()


def test_a_second_exchange_handoff_cites_policy_4_4():
    tools, ctx = escalate(customer_id="C-103")
    tools["escalate_to_human"].invoke(
        {
            "reason": "second_exchange",
            "summary": "Second exchange requested.",
            "order_id": "TR-4528",
            "required_human_action": "Approve or deny the second exchange.",
        }
    )
    assert "4.4" in ctx.actions[0]["details"]["structured"]["policy_sections"]


def test_an_eligibility_verdict_is_carried_into_the_handoff():
    """So the human does not re-run a check that already has an answer."""
    tools, ctx = escalate(customer_id="C-102")
    tools["get_order"].invoke({"order_id": "TR-4527"})
    tools["check_return_eligibility"].invoke({"order_id": "TR-4527", "item_id": "TR-EAR-042"})
    tools["escalate_to_human"].invoke(
        {"reason": "customer_disputes_outcome", "summary": "Customer disputes the refusal."}
    )
    structured = ctx.actions[0]["details"]["structured"]
    assert "non_returnable_category" in (structured.get("eligibility_result") or "")


def test_an_attempted_action_and_its_result_are_carried():
    pending = PendingAction(
        customer_id="C-101", kind="return", order_id="TR-4530", item_id="TR-KRT-033"
    )
    ctx = ToolContext.build("C-101", AS_OF, user_confirmed=True, pending=pending)
    tools = {t.name: t for t in build_toolset(ctx)}
    tools["get_order"].invoke({"order_id": "TR-4530"})
    tools["check_return_eligibility"].invoke({"order_id": "TR-4530", "item_id": "TR-KRT-033"})
    created = tools["initiate_return"].invoke({"order_id": "TR-4530", "item_id": "TR-KRT-033"})
    tools["escalate_to_human"].invoke(
        {"reason": "customer_wants_pickup_change", "summary": "Customer needs a different window."}
    )

    structured = [a for a in ctx.actions if a["type"] == "escalated"][0]["details"]["structured"]
    assert structured["action_attempted"] == "initiate_return"
    assert structured["action_result"] == created["reference"]


# ------------------------------------------------------------- what is excluded


def test_a_handoff_never_carries_contact_details_or_banking_content():
    """The colleague looks the customer up by id; the summary is not a data dump."""
    tools, ctx = escalate()
    tools["get_order"].invoke({"order_id": "TR-4526"})
    tools["escalate_to_human"].invoke(
        {"reason": "lost_parcel", "summary": "Carrier marked lost.", "order_id": "TR-4526"}
    )
    blob = json.dumps(ctx.actions[0]["details"]).lower()

    for forbidden in ("marcus.bell@example.com", "+1-415-555-0102", "marcus bell"):
        assert forbidden.lower() not in blob
    for forbidden in ("cvv", "card number", "account number", "ifsc"):
        assert forbidden not in blob


def test_the_summary_only_holds_what_the_turn_supplied():
    """No field is populated from anywhere but this turn's context and arguments."""
    tools, ctx = escalate()
    tools["escalate_to_human"].invoke({"reason": "policy_not_covered", "summary": "gift wrapping"})
    structured = ctx.actions[0]["details"]["structured"]

    assert structured["customer_id"] == "C-101"
    assert structured.get("order_id") is None
    assert structured["facts_checked"] == []
    assert structured.get("action_attempted") is None


def test_a_lost_parcel_handoff_relays_the_clause_not_an_invented_outcome():
    """The agent cited 1.6 without ever reading it, then improvised the outcome.

    A live run promised "a colleague will process your refund to your credit
    card". Policy 1.6 makes that the customer's choice between a replacement and
    a refund, within a stated window — so the reply committed a colleague to one
    outcome and dropped what the customer was actually entitled to.
    """
    from datetime import date

    from app.tools import build_toolset
    from app.tools.context import ToolContext

    ctx = ToolContext.build("C-101", date(2026, 7, 29))
    escalate = next(t for t in build_toolset(ctx) if t.name == "escalate_to_human")
    result = escalate.invoke(
        {"reason": "lost_parcel", "summary": "Parcel never arrived.", "order_id": "TR-4526"}
    )

    guidance = result["guidance"]
    assert "replacement" in guidance and "refund" in guidance, "clause text was not handed over"
    assert "5 business days" in guidance, "the stated timeline was not handed over"
    assert "do not" in guidance.lower() and "outcome" in guidance.lower()


def test_the_clause_is_read_from_the_document_not_restated():
    """It must track the policy file, so an edited clause is relayed correctly."""
    from app.tools.escalation import _clause_text

    assert "lost-parcel claim" in _clause_text("1.6").lower()
    assert _clause_text("9.9") == ""
