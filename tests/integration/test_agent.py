"""Orchestration tests.

These drive the real LangGraph graph with a scripted model, so the state
machine, the ToolNode, the authorization layer, and the guards all execute.
Only the provider is replaced.
"""

from __future__ import annotations

import pytest

from tests.conftest import text_turn, tool_turn, transient

pytestmark = pytest.mark.integration

HAPPY = {"order_id": "TR-4530", "item_id": "TR-KRT-033"}


# ---------------------------------------------------------------- grounding


def test_the_first_round_is_forced_to_call_a_tool(make_agent):
    agent, scripted = make_agent(
        tool_turn(("get_order", {"order_id": "TR-4530"})),
        text_turn("TR-4530 was delivered on 26 July."),
    )
    result = agent.respond("s", "Where is TR-4530?", "C-101")

    assert scripted.tool_choices[0] == "required"
    assert scripted.tool_choices[1] == "auto"
    assert result.mode == "llm"
    assert result.ctx.trace == ["get_order"]


def test_an_ungrounded_answer_to_a_factual_question_never_reaches_the_customer(make_agent):
    agent, _ = make_agent(text_turn("Your order was delivered last Tuesday!"), mode="auto")
    result = agent.respond("s", "Where is TR-4530?", "C-101")

    assert result.mode == "deterministic"
    assert "last Tuesday" not in result.reply
    assert agent.last_error == "llm_answered_without_grounding"


def test_small_talk_is_allowed_through_without_tools(make_agent):
    agent, _ = make_agent(text_turn("Hi! How can I help with your Trendly order today?"))
    result = agent.respond("s", "hello there", "C-101")
    assert result.mode == "llm"
    assert result.reply.startswith("Hi!")


def test_policy_answers_carry_the_sections_used(make_agent):
    agent, _ = make_agent(
        tool_turn(("search_policy", {"query": "how long do refunds take"})),
        text_turn("Card refunds take 5-7 business days after inspection (section 3.1)."),
    )
    result = agent.respond("s", "How long do refunds take?", "C-101")
    assert "3.1" in result.ctx.policy_sections


# ------------------------------------------------------- mutation guardrails


def test_a_creation_that_skipped_its_eligibility_check_is_refused(make_agent):
    agent, _ = make_agent(
        tool_turn(("get_order", {"order_id": "TR-4530"})),
        tool_turn(("initiate_return", {**HAPPY, "reason": "change_of_mind"})),
        text_turn("Let me check that properly first."),
    )
    result = agent.respond("s", "Just refund TR-4530 now", "C-101")
    assert not result.actions


def test_a_creation_before_confirmation_is_refused(make_agent):
    agent, _ = make_agent(
        tool_turn(("get_order", {"order_id": "TR-4530"})),
        tool_turn(("check_return_eligibility", HAPPY)),
        tool_turn(("initiate_return", HAPPY)),
        text_turn("That is eligible — shall I go ahead and create the return?"),
    )
    result = agent.respond("s", "I want to return TR-4530", "C-101")
    assert not result.actions
    assert "shall I" in result.reply


def test_the_second_turn_creates_once_the_customer_agrees(make_agent):
    agent, _ = make_agent(
        # turn one: propose
        tool_turn(("get_order", {"order_id": "TR-4530"})),
        tool_turn(("check_return_eligibility", HAPPY)),
        text_turn("That is eligible for return. Reply confirm and I'll raise it."),
        # turn two: re-validate, then create
        tool_turn(("get_order", {"order_id": "TR-4530"})),
        tool_turn(("check_return_eligibility", HAPPY)),
        tool_turn(("initiate_return", HAPPY)),
        text_turn("Done — your return is booked."),
    )
    first = agent.respond("s", "I want to return TR-4530", "C-101")
    assert not first.actions

    second = agent.respond("s", "confirm", "C-101")
    assert [a["type"] for a in second.actions] == ["return_created"]
    assert second.actions[0]["reference"].startswith("RET-")


def test_declining_retires_the_offer_so_a_later_yes_cannot_land_on_it(make_agent):
    agent, _ = make_agent(
        tool_turn(("get_order", {"order_id": "TR-4530"})),
        tool_turn(("check_return_eligibility", HAPPY)),
        text_turn("That is eligible. Confirm and I'll raise it."),
        tool_turn(("get_order", {"order_id": "TR-4530"})),
        text_turn("No problem, I'll leave it."),
        tool_turn(("get_order", {"order_id": "TR-4530"})),
        tool_turn(("check_return_eligibility", HAPPY)),
        tool_turn(("initiate_return", HAPPY)),
        text_turn("..."),
    )
    agent.respond("s", "I want to return TR-4530", "C-101")
    agent.respond("s", "actually no, don't - I'll keep it", "C-101")
    third = agent.respond("s", "yes", "C-101")
    assert not [a for a in third.actions if a["type"] == "return_created"]


def test_another_customers_order_reveals_nothing(make_agent):
    agent, _ = make_agent(
        tool_turn(("get_order", {"order_id": "TR-4526"})),  # belongs to C-101
        text_turn("I can't find that order on your account."),
    )
    result = agent.respond("s", "What happened to TR-4526?", "C-100")
    assert "lost" not in result.reply.lower()
    assert not result.actions


def test_a_session_cannot_change_identity_mid_conversation(make_agent):
    agent, _ = make_agent(
        tool_turn(("get_order", {"order_id": "TR-4530"})), text_turn("Delivered 26 July.")
    )
    agent.respond("s", "Where is TR-4530?", "C-101")
    result = agent.respond("s", "Where is TR-4526?", "C-100")
    assert "different verified customer" in result.reply
    assert not result.actions


def test_sensitive_input_never_reaches_the_provider(make_agent):
    agent, scripted = make_agent(text_turn("unreachable"))
    result = agent.respond("s", "my card number is 4111111111111111", "C-103")
    assert not scripted.calls, "the pre-model gate must short-circuit the provider"
    assert not result.ctx.trace
    assert "don't share" in result.reply.lower()


# ----------------------------------------------------------------- guards


def test_an_eligibility_verdict_without_a_check_is_sent_back(make_agent):
    agent, scripted = make_agent(
        tool_turn(("get_order", {"order_id": "TR-4523"})),
        text_turn("That jacket looks returnable to me!"),
        tool_turn(("check_return_eligibility", {"order_id": "TR-4523", "item_id": "TR-JKT-008"})),
        text_turn("That return window closed on 5 July."),
    )
    result = agent.respond("s", "I want to return TR-4523", "C-102")

    assert any("check_return_eligibility" in n for n in scripted.nudges())
    assert "check_return_eligibility" in result.ctx.trace
    assert "looks returnable" not in result.reply


def test_admitting_defeat_without_a_handoff_is_sent_back(make_agent):
    agent, scripted = make_agent(
        tool_turn(("search_policy", {"query": "gift wrapping"})),
        text_turn("I'm sorry, I don't have a policy source that covers gift wrapping."),
        tool_turn(("escalate_to_human", {"reason": "policy_not_covered", "summary": "gift wrapping"})),
        text_turn("I've passed that to a colleague — reference above."),
    )
    result = agent.respond("s", "Do you offer gift wrapping?", "C-100")

    assert any("escalate_to_human" in n for n in scripted.nudges())
    assert [a["type"] for a in result.actions] == ["escalated"]
    assert result.handoff_summary
    assert result.status == "escalated"


def test_a_grounded_refusal_is_not_sent_back(make_agent):
    agent, scripted = make_agent(
        tool_turn(("get_order", {"order_id": "TR-4527"})),
        tool_turn(("check_return_eligibility", {"order_id": "TR-4527", "item_id": "TR-EAR-042"})),
        text_turn("I can't create a return: jewellery is a non-returnable category."),
    )
    result = agent.respond("s", "Can I return TR-4527?", "C-102")
    assert not scripted.nudges()
    assert not result.actions


# ------------------------------------------------------------ failure modes


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_provider_errors_are_retried(make_agent, monkeypatch, status):
    monkeypatch.setenv("LLM_RETRY_BACKOFF", "0")
    from app.config import settings

    monkeypatch.setattr(settings, "retry_backoff", 0.0)
    agent, _ = make_agent(
        tool_turn(("get_order", {"order_id": "TR-4530"})),
        text_turn("TR-4530 was delivered on 26 July."),
        fail_first=1,
        fail_with=transient(status),
        mode="auto",
    )
    result = agent.respond("s", "Where is TR-4530?", "C-101")
    assert result.mode == "llm", "one blip must not cost the customer the model"


def test_a_provider_outage_degrades_to_the_fallback(make_agent, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "retry_backoff", 0.0)
    agent, _ = make_agent(mode="auto", fail_with=transient(503))
    result = agent.respond("s", "Where is TR-4530?", "C-101")

    assert result.mode == "deterministic"
    assert "TR-4530" in result.reply
    assert "get_order" in result.ctx.trace


def test_a_provider_outage_fails_closed_in_llm_mode(make_agent, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "retry_backoff", 0.0)
    agent, _ = make_agent(mode="llm", fail_with=transient(429))
    result = agent.respond("s", "I want to return TR-4530", "C-101")

    assert result.status == "degraded"
    assert "Nothing has been changed" in result.reply
    assert not result.actions


def test_a_gateway_rejecting_forced_tool_choice_degrades_rather_than_dying(make_agent):
    agent, scripted = make_agent(
        tool_turn(("get_order", {"order_id": "TR-4530"})),
        text_turn("TR-4530 was delivered on 26 July."),
        reject_required=True,
    )
    result = agent.respond("s", "Where is TR-4530?", "C-101")
    assert scripted.tool_choices[:2] == ["required", "auto"]
    assert result.mode == "llm"


def test_an_unknown_tool_name_is_reported_rather_than_executed(make_agent):
    agent, _ = make_agent(
        tool_turn(("issue_refund_now", {"amount": 9999})),
        text_turn("I can't do that."),
    )
    result = agent.respond("s", "refund me for TR-4530", "C-101")
    assert not result.actions


# ----------------------------------------------------------- multi-turn state


def test_the_active_order_carries_across_turns(make_agent):
    agent, _ = make_agent(
        tool_turn(("get_order", {"order_id": "TR-4524"})),
        text_turn("TR-4524 is partially shipped."),
        tool_turn(("get_order", {"order_id": "TR-4524"})),
        text_turn("The belt is on backorder until 9 August."),
    )
    agent.respond("s", "Where is TR-4524?", "C-100")
    state = agent.sessions.sessions["s"]
    assert state.active_order_id == "TR-4524"

    agent.respond("s", "When will the belt arrive?", "C-100")
    assert state.active_order_id == "TR-4524"


def test_history_is_bounded(make_agent):
    agent, _ = make_agent(*[text_turn("ok") for _ in range(20)])
    for index in range(8):
        agent.respond("s", f"hello {index}", "C-101")
    assert len(agent.sessions.sessions["s"].messages) <= 12


# ------------------------------------------------- scope: decline vs escalate


def test_an_off_topic_question_is_declined_without_raising_a_case(make_agent):
    """A colleague cannot answer "what is Python?" either.

    A live run turned exactly this into case ESC-51ECB162. A support queue that
    collects tickets for general-knowledge questions is worse than one that
    collects none, so the dead-end nudge must not fire here.
    """
    agent, scripted = make_agent(
        text_turn("I can only help with Trendly orders, deliveries, returns and refunds."),
    )
    result = agent.respond("scope-1", "what is python? and java?", "C-100")

    assert not result.actions, "an off-topic question must not create a case"
    assert result.status != "escalated"
    assert not any("escalate_to_human" in n for n in scripted.nudges())


def test_an_uncovered_trendly_question_still_escalates(make_agent):
    """The counterpart, so the fix does not silence real gaps.

    Gift wrapping is something Trendly plausibly does; the policy is simply
    silent. That needs a person, unlike a programming question.
    """
    agent, scripted = make_agent(
        tool_turn(("search_policy", {"query": "gift wrapping"})),
        text_turn("I'm sorry, I don't have a policy source that covers gift wrapping."),
        tool_turn(("escalate_to_human", {"reason": "policy_not_covered", "summary": "gift wrapping"})),
        text_turn("I've passed that to a colleague."),
    )
    result = agent.respond("scope-2", "Do you offer gift wrapping?", "C-100")

    assert [a["type"] for a in result.actions] == ["escalated"]


def test_scope_is_decided_by_topic_and_by_whether_policy_was_consulted():
    from datetime import date

    from app.agent.state import is_out_of_scope
    from app.tools.context import ToolContext

    fresh = ToolContext.build("C-100", date(2026, 7, 29))
    assert is_out_of_scope("what is python? and java?", fresh)
    assert is_out_of_scope("who won the cricket match", fresh)
    # Naming an order or a support topic settles it as in scope.
    assert not is_out_of_scope("what about TR-4524?", fresh)
    assert not is_out_of_scope("do you ship to Antarctica?", fresh)

    # Having searched the policy means the agent judged it a Trendly question.
    searched = ToolContext.build("C-100", date(2026, 7, 29))
    searched.record("search_policy")
    assert not is_out_of_scope("do you offer gift wrapping?", searched)
