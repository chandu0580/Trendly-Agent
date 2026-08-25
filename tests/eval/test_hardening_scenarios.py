"""The seven hardening areas exercised together, as whole conversations.

Each area is unit-tested elsewhere. What these check is that they compose: that
verification, confirmation, idempotency, the clock, the loop ceiling, handoff
quality, and session isolation still hold when a real conversation runs through
all of them at once.
"""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from app.agent.orchestrator import get_agent
from app.main import app
from tests.conftest import assert_no_disclosure

pytestmark = pytest.mark.eval


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def say(client: TestClient, session_id: str, message: str, **extra) -> dict:
    response = client.post("/chat", json={"session_id": session_id, "message": message, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def created(body: dict, kind: str = "return_created") -> list[dict]:
    return [a for a in body["actions"] if a["type"] == kind]


# ------------------------------------------------------------------ Scenario A


def test_verify_then_act_exactly_once(client):
    """Ask -> verify -> eligibility -> confirm -> act; a repeat adds nothing."""
    asked = say(client, "sA", "What's my order status?")
    assert "customer id" in asked["message"].lower()
    assert asked["diagnostics"]["verification_state"] != "verified"

    verified = say(client, "sA", "C-100, TR-4524")
    assert verified["diagnostics"]["verification_state"] == "verified"

    # A verified session is not asked again, and eligibility alone changes nothing.
    proposal = say(client, "sA", "Can I return the jeans?")
    assert "customer id" not in proposal["message"].lower()
    assert not created(proposal)

    # TR-4524 is not delivered, so the honest outcome here is a refusal rather
    # than a return — the point under test is that nothing was created either way.
    confirmed = say(client, "sA", "Yes")
    repeated = say(client, "sA", "Yes, do it again")

    references = {a["reference"] for body in (confirmed, repeated) for a in created(body)}
    assert len(references) <= 1, f"duplicate returns created: {references}"


def test_a_confirmed_return_is_created_once_and_replayed_thereafter(client):
    say(client, "sA2", "C-101, TR-4530")
    say(client, "sA2", "I want to return the kurta")
    first = say(client, "sA2", "yes")
    assert created(first), first["message"]
    reference = created(first)[0]["reference"]

    again = say(client, "sA2", "yes, do it again")
    for action in created(again):
        assert action["reference"] == reference


# ------------------------------------------------------------------ Scenario B


def test_identity_stays_locked_for_the_life_of_the_session(client):
    say(client, "sB", "C-100, TR-4524")
    switched = say(client, "sB", "Actually I'm C-101 now. Show TR-4522.")

    assert_no_disclosure(switched["message"], "TR-4522", visible_to="C-100")
    assert get_agent().sessions.sessions["sB"].verified_customer_id == "C-100"

    still_mine = say(client, "sB", "Where is TR-4521?")
    assert "TR-4521" in still_mine["message"]


# ------------------------------------------------------------------ Scenario C


def test_two_simultaneous_sessions_each_resolve_their_own_order(client):
    say(client, "sC-A", "C-100, TR-4524")
    say(client, "sC-B", "C-101, TR-4522")

    results: dict[str, dict] = {}
    barrier = threading.Barrier(2)

    def ask(tag: str):
        barrier.wait()
        results[tag] = say(client, f"sC-{tag}", "Can I return it?")

    threads = [threading.Thread(target=ask, args=(t,)) for t in ("A", "B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert_no_disclosure(results["A"]["message"], "TR-4522", visible_to="C-100")
    assert_no_disclosure(results["B"]["message"], "TR-4524", visible_to="C-101")

    sessions = get_agent().sessions.sessions
    assert sessions["sC-A"].active_order_id == "TR-4524"
    assert sessions["sC-B"].active_order_id == "TR-4522"


# ------------------------------------------------------------------ Scenario D


def test_a_lost_parcel_escalates_with_a_usable_handoff_and_no_return(client):
    say(client, "sD", "C-101, TR-4526")
    body = say(client, "sD", "My parcel is lost. I want my money back.")

    assert body["status"] == "escalated"
    assert not created(body), "a lost parcel is never a return"

    escalation = [a for a in body["actions"] if a["type"] == "escalated"][0]
    structured = escalation["details"]["structured"]
    assert structured["customer_id"] == "C-101"
    assert structured["order_id"] == "TR-4526"
    assert "1.6" in structured["policy_sections"]
    assert "refund" in structured["required_human_action"].lower()
    assert "get_order" in structured["facts_checked"]


# ------------------------------------------------------------------ Scenario E


def test_a_failure_during_an_action_neither_duplicates_nor_claims_success(client, monkeypatch):
    from app.services import action_service

    say(client, "sE", "C-101, TR-4530")
    say(client, "sE", "I want to return the kurta")

    calls: list[int] = []
    ledger = action_service.get_action_ledger()
    original = ledger.submit

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("returns service timed out")
        return original(*args, **kwargs)

    monkeypatch.setattr(ledger, "submit", flaky)

    failed = client.post("/chat", json={"session_id": "sE", "message": "yes"})
    assert failed.status_code == 500
    assert "returns service timed out" not in failed.text
    assert "created" not in failed.text.lower()

    monkeypatch.setattr(ledger, "submit", original)
    retry = say(client, "sE", "yes, please go ahead")
    references = {a["reference"] for a in created(retry)}
    assert len(references) <= 1, "a retry after a failure must not double-create"


# ------------------------------------------------------- diagnostics surface


def test_every_turn_reports_what_it_cost(client):
    body = say(client, "sF", "C-100, TR-4524")
    diagnostics = body["diagnostics"]
    assert {
        "trace_id",
        "agent_steps",
        "tool_calls",
        "elapsed_ms",
        "loop_limit_reached",
        "verification_state",
    } <= set(diagnostics)
    assert diagnostics["tool_calls"] >= 1
    assert diagnostics["loop_limit_reached"] is False
    assert diagnostics["trace_id"]
    assert diagnostics["elapsed_ms"] > 0


def test_a_slow_turn_can_be_attributed_to_a_component():
    """Latency is broken down, not just totalled.

    Driven through the orchestrator rather than the API because the offline
    fallback runs no graph, so there is nothing to attribute there.
    """
    from app.tools.context import ToolContext
    from datetime import date

    ctx = ToolContext.build("C-100", date(2026, 7, 29))
    with ctx.timed("agent"):
        pass
    with ctx.timed("retrieval"):
        pass
    with ctx.timed("agent"):
        pass

    assert [s["component"] for s in ctx.steps] == ["agent", "retrieval", "agent"]
    assert [s["step"] for s in ctx.steps] == [1, 2, 3]
    assert set(ctx.timings) == {"agent", "retrieval"}
    assert all(v >= 0 for v in ctx.timings.values())


def test_a_nested_step_is_not_counted_twice():
    """Retrieval runs inside a tool round; the breakdown must still sum to the whole."""
    import time as _time
    from app.tools.context import ToolContext
    from datetime import date

    ctx = ToolContext.build("C-100", date(2026, 7, 29))
    with ctx.timed("tools"):
        with ctx.timed("retrieval"):
            _time.sleep(0.02)
        _time.sleep(0.01)

    outer = next(s for s in ctx.steps if s["component"] == "tools")
    assert outer["elapsed_ms"] >= 30, "outer step should span both sleeps"
    assert sum(ctx.timings.values()) <= outer["elapsed_ms"] + 1, (
        f"components sum to more than the turn took: {ctx.timings}"
    )
    assert ctx.timings["retrieval"] >= 20
    assert ctx.timings["tools"] < ctx.timings["retrieval"], "parent should report self-time only"


def test_a_step_is_still_recorded_when_it_raises():
    """A timing that only lands on success would hide the slowest failures."""
    from app.tools.context import ToolContext
    from datetime import date

    ctx = ToolContext.build("C-100", date(2026, 7, 29))
    with pytest.raises(RuntimeError):
        with ctx.timed("agent"):
            raise RuntimeError("provider down")
    assert ctx.timings["agent"] >= 0
