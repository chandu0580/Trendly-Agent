"""Safety guards and the signals that route a turn back for grounding."""

from __future__ import annotations

import pytest

from app.agent.state import (
    contains_sensitive_data,
    is_dead_end,
    needs_grounding,
    unchecked_eligibility,
)
from app.tools import build_toolset
from app.tools.context import ToolContext
from tests.conftest import AS_OF

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "message",
    [
        "my card number is 4111111111111111",
        "CVV is 123",
        "my bank account number is 12345678901234",
        "IFSC code HDFC0001234",
        "account number 998877665544332",
    ],
)
def test_banking_content_is_detected(message):
    assert contains_sensitive_data(message)


@pytest.mark.parametrize(
    "message",
    [
        "where is TR-4530",
        "I want a refund to my card",
        "my order total was 2199",
        "can I pay cash on delivery",
    ],
)
def test_ordinary_messages_are_not_flagged_as_banking_content(message):
    assert not contains_sensitive_data(message)


@pytest.mark.parametrize(
    "message",
    ["where is TR-4530", "can I return this", "what is your refund policy", "track my order"],
)
def test_factual_questions_require_grounding(message):
    assert needs_grounding(message)


@pytest.mark.parametrize("message", ["hello there", "thanks, bye", "who are you"])
def test_small_talk_does_not_require_grounding(message):
    assert not needs_grounding(message)


# ------------------------------------------------------------- dead-end guard


@pytest.mark.parametrize(
    "reply",
    [
        "I'm sorry, but I don't have a policy source that covers gift wrapping.",
        "That is not covered by the policy.",
        "I can't answer that from our policy.",
        "The policy is silent on that.",
        "Unfortunately I cannot help with that request.",
    ],
)
def test_admitting_defeat_without_a_handoff_is_caught(reply):
    ctx = ToolContext.build("C-101", AS_OF)
    assert is_dead_end(reply, ctx)


@pytest.mark.parametrize(
    "reply",
    [
        "Your return has been created: RET-123.",
        "That item is eligible for return.",
        "I can't create a return for the earrings: jewellery is a non-returnable category.",
        "Returns are allowed within 30 calendar days of delivery.",
        "I can't offer discounts, but I've applied the policy store credit.",
    ],
)
def test_a_grounded_refusal_is_not_a_dead_end(reply):
    """These are answers. Escalating them would be wrong."""
    ctx = ToolContext.build("C-101", AS_OF)
    assert not is_dead_end(reply, ctx)


def test_a_reply_that_already_escalated_is_never_a_dead_end():
    ctx = ToolContext.build("C-101", AS_OF)
    tools = {t.name: t for t in build_toolset(ctx)}
    tools["escalate_to_human"].invoke({"reason": "policy_not_covered", "summary": "gift wrapping"})
    assert not is_dead_end("I don't have a policy that covers that.", ctx)


# --------------------------------------------------- eligibility backstop


def test_an_eligibility_question_answered_from_lookup_alone_is_caught():
    ctx = ToolContext.build("C-101", AS_OF)
    tools = {t.name: t for t in build_toolset(ctx)}
    tools["get_order"].invoke({"order_id": "TR-4530"})
    assert unchecked_eligibility("I want to return TR-4530", ctx) == ["check_return_eligibility"]


def test_running_the_check_satisfies_the_backstop():
    ctx = ToolContext.build("C-101", AS_OF)
    tools = {t.name: t for t in build_toolset(ctx)}
    tools["get_order"].invoke({"order_id": "TR-4530"})
    tools["check_return_eligibility"].invoke({"order_id": "TR-4530", "item_id": "TR-KRT-033"})
    assert unchecked_eligibility("I want to return TR-4530", ctx) == []


def test_an_escalated_turn_is_exempt():
    """A lost parcel is not an eligibility question; forcing a check there would
    contradict the lost-parcel rule."""
    ctx = ToolContext.build("C-101", AS_OF)
    tools = {t.name: t for t in build_toolset(ctx)}
    tools["get_order"].invoke({"order_id": "TR-4526"})
    tools["escalate_to_human"].invoke({"reason": "lost_parcel", "summary": "carrier marked lost"})
    assert unchecked_eligibility("TR-4526 is lost, I want my money back", ctx) == []


def test_no_order_in_play_means_nothing_to_check():
    ctx = ToolContext.build("C-101", AS_OF)
    assert unchecked_eligibility("what is your return policy", ctx) == []


# ------------------------------------------------- abandoned-turn cleanup


def test_a_retry_context_drops_provisional_escalations_but_keeps_mutations():
    """One question must not raise two tickets because a turn died mid-flight,
    but a return already written to the ledger must still be reported."""
    ctx = ToolContext.build("C-101", AS_OF)
    ctx.add_action("escalated", "ESC-1", {"summary": "provisional"})
    ctx.add_action("return_created", "RET-1", {"order_id": "TR-4530"})

    retry = ctx.for_retry()
    assert [a["type"] for a in retry.actions] == ["return_created"]
    assert retry.trace == ctx.trace
