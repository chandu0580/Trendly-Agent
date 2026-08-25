"""The hard ceiling on agent work within one turn.

The model is never trusted to stop itself. Every path out of the loop is
bounded, and hitting the ceiling produces an honest handoff rather than an
invented result.
"""

from __future__ import annotations

import pytest

from app.config import settings
from tests.conftest import text_turn, tool_turn, transient

pytestmark = pytest.mark.integration

LOOKUP = ("get_order", {"order_id": "TR-4530"})
CHECK = ("check_return_eligibility", {"order_id": "TR-4530", "item_id": "TR-KRT-033"})


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(settings, "retry_backoff", 0.0)


# ------------------------------------------------------- legitimate flows


def test_a_single_tool_turn_is_well_inside_the_budget(make_agent):
    agent, _ = make_agent(tool_turn(LOOKUP), text_turn("TR-4530 was delivered on 26 July."))
    result = agent.respond("s", "Where is TR-4530?", "C-101")

    assert result.mode == "llm"
    assert result.ctx.agent_steps == 2
    assert result.ctx.tool_calls == 1
    assert not result.ctx.loop_limit_reached


def test_a_multi_tool_turn_is_still_inside_the_budget(make_agent):
    agent, _ = make_agent(
        tool_turn(LOOKUP),
        tool_turn(CHECK),
        text_turn("That is eligible. Shall I raise it?"),
    )
    result = agent.respond("s", "Can I return TR-4530?", "C-101")

    assert result.ctx.tool_calls == 2
    assert result.ctx.agent_steps <= settings.max_agent_steps
    assert not result.ctx.loop_limit_reached


def test_a_guard_retry_still_fits(make_agent):
    """agent -> tool -> agent -> guard -> agent -> tool -> agent must not trip the cap."""
    agent, _ = make_agent(
        tool_turn(LOOKUP),
        text_turn("That jacket looks returnable to me!"),  # ungrounded, sent back
        tool_turn(CHECK),
        text_turn("That is eligible."),
    )
    result = agent.respond("s", "I want to return TR-4530", "C-101")

    assert not result.ctx.loop_limit_reached
    assert "check_return_eligibility" in result.ctx.trace


# ------------------------------------------------------------- the ceiling


def test_a_model_that_keeps_asking_for_tools_is_stopped(make_agent):
    """The classic runaway: the same tool, forever."""
    agent, _ = make_agent(*[tool_turn(LOOKUP) for _ in range(40)])
    result = agent.respond("s", "Where is TR-4530?", "C-101")

    assert result.ctx.loop_limit_reached
    assert result.ctx.agent_steps == settings.max_agent_steps
    assert "escalated" in [a["type"] for a in result.actions]
    assert result.handoff_summary


def test_the_ceiling_produces_an_honest_reply_not_an_invented_one(make_agent):
    agent, _ = make_agent(*[tool_turn(LOOKUP) for _ in range(40)])
    result = agent.respond("s", "Where is TR-4530?", "C-101")

    assert "specialist" in result.reply.lower() or "support" in result.reply.lower()
    assert not [a for a in result.actions if a["type"] in {"return_created", "exchange_created"}]


def test_the_limit_is_configurable_and_respected(make_agent, monkeypatch):
    monkeypatch.setattr(settings, "max_agent_steps", 3)
    agent, _ = make_agent(*[tool_turn(LOOKUP) for _ in range(40)])
    result = agent.respond("s", "Where is TR-4530?", "C-101")

    assert result.ctx.agent_steps == 3
    assert result.ctx.loop_limit_reached


def test_the_loop_terminates_rather_than_hanging(make_agent):
    """A bounded loop must finish; an unbounded one exhausts the script instead."""
    agent, scripted = make_agent(*[tool_turn(LOOKUP) for _ in range(40)])
    agent.respond("s", "Where is TR-4530?", "C-101")
    assert len(scripted.calls) <= settings.max_agent_steps


# ------------------------------------------------------- bounded recovery


def test_provider_retries_are_bounded_then_degrade(make_agent):
    agent, scripted = make_agent(mode="auto", fail_with=transient(503))
    result = agent.respond("s", "Where is TR-4530?", "C-101")

    assert len(scripted.calls) == settings.transient_retries + 1
    assert result.mode == "deterministic", "it stops retrying and answers safely"


def test_guard_retries_are_bounded(make_agent):
    """A model that keeps admitting defeat is nudged twice, then let go."""
    agent, scripted = make_agent(
        tool_turn(("search_policy", {"query": "gift wrapping"})),
        *[text_turn("That is not covered by the policy.")] * 6,
        mode="auto",
    )
    result = agent.respond("s", "Do you offer gift wrapping?", "C-100")

    assert len(scripted.nudges()) <= settings.max_nudges
    assert not result.ctx.loop_limit_reached or result.handoff_summary


def test_repeated_retrieval_failure_does_not_spin(make_agent, monkeypatch):
    from app.services import policy_service

    monkeypatch.setattr(
        policy_service,
        "get_retriever",
        lambda: type("R", (), {"search": staticmethod(lambda _q: (_ for _ in ()).throw(RuntimeError("down")))})(),
    )
    agent, _ = make_agent(
        *[tool_turn(("search_policy", {"query": "returns"})) for _ in range(40)]
    )
    result = agent.respond("s", "What is your return policy?", "C-101")

    assert result.ctx.loop_limit_reached
    assert result.ctx.agent_steps == settings.max_agent_steps


def test_step_and_tool_counts_are_reported(make_agent):
    agent, _ = make_agent(tool_turn(LOOKUP), tool_turn(CHECK), text_turn("Eligible."))
    result = agent.respond("s", "Can I return TR-4530?", "C-101")

    assert result.ctx.agent_steps > 0
    assert result.ctx.tool_calls == len(result.ctx.trace)
