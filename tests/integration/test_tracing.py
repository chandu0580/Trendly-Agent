"""Per-run correlation.

A run should be reconstructable after the fact: one id, threaded through the
context, the logs, and the response.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import text_turn, tool_turn

pytestmark = pytest.mark.integration


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def say(client: TestClient, session_id: str, message: str, **extra) -> dict:
    response = client.post("/chat", json={"session_id": session_id, "message": message, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def test_every_request_gets_a_trace_id(client):
    body = say(client, "tr-1", "Where is TR-4521?", customer_id="C-100")
    trace = body["diagnostics"]["trace_id"]
    assert trace and len(trace) >= 8


def test_two_requests_get_different_trace_ids(client):
    first = say(client, "tr-2", "Where is TR-4521?", customer_id="C-100")
    second = say(client, "tr-3", "Where is TR-4521?", customer_id="C-100")
    assert first["diagnostics"]["trace_id"] != second["diagnostics"]["trace_id"]


def test_turns_in_one_session_are_still_separate_runs(client):
    """A trace identifies a run, not a conversation."""
    first = say(client, "tr-4", "Where is TR-4521?", customer_id="C-100")
    second = say(client, "tr-4", "And TR-4524?", customer_id="C-100")
    assert first["diagnostics"]["trace_id"] != second["diagnostics"]["trace_id"]


def test_the_trace_is_stable_within_one_run(make_agent):
    """The id on the context is the id reported back."""
    agent, _ = make_agent(
        tool_turn(("get_order", {"order_id": "TR-4530"})), text_turn("Delivered 26 July.")
    )
    result = agent.respond("tr-5", "Where is TR-4530?", "C-101")
    assert result.ctx.trace_id == result.trace_id
    assert result.trace_id


def test_a_caller_supplied_trace_is_honoured(make_agent):
    """So an upstream request id can be threaded through."""
    agent, _ = make_agent(
        tool_turn(("get_order", {"order_id": "TR-4530"})), text_turn("Delivered.")
    )
    result = agent.respond("tr-6", "Where is TR-4530?", "C-101", trace_id="upstream-abc123")
    assert result.trace_id == "upstream-abc123"
    assert result.ctx.trace_id == "upstream-abc123"


def test_the_trace_appears_on_guard_and_fallback_log_events(make_agent, caplog):
    """Guard and recovery events must correlate, not just the final line."""
    agent, _ = make_agent(text_turn("Your order was delivered last Tuesday!"), mode="auto")
    with caplog.at_level(logging.INFO, logger="trendly.agent"):
        result = agent.respond("tr-7", "Where is TR-4530?", "C-101")

    correlated = [r.getMessage() for r in caplog.records if result.trace_id in r.getMessage()]
    assert correlated, f"no log line carried trace {result.trace_id}"
    assert any("ungrounded" in m for m in correlated)


def test_the_final_log_line_carries_the_whole_run(make_agent, caplog):
    agent, _ = make_agent(
        tool_turn(("get_order", {"order_id": "TR-4530"})), text_turn("Delivered.")
    )
    with caplog.at_level(logging.INFO, logger="trendly.agent"):
        result = agent.respond("tr-8", "Where is TR-4530?", "C-101")

    summary = [r.getMessage() for r in caplog.records if "mode=" in r.getMessage()]
    assert summary
    line = summary[-1]
    for expected in (result.trace_id, "tr-8", "get_order", "steps="):
        assert expected in line, f"{expected!r} missing from: {line}"


def test_sensitive_input_is_not_written_to_the_trace(client, caplog):
    """The pre-model gate refuses it; the card number must not be logged either."""
    with caplog.at_level(logging.INFO, logger="trendly.agent"):
        body = say(client, "tr-9", "my card number is 4111111111111111", customer_id="C-103")

    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "4111111111111111" not in logged
    assert "4111111111111111" not in str(body)
    assert not body["tool_trace"]


def test_elapsed_time_is_measured_and_reported(client):
    body = say(client, "tr-10", "Where is TR-4521?", customer_id="C-100")
    assert body["diagnostics"]["elapsed_ms"] > 0


def test_the_response_reports_the_work_done(client):
    body = say(client, "tr-11", "Can I return TR-4530?", customer_id="C-101")
    diagnostics = body["diagnostics"]
    assert diagnostics["tool_calls"] == len(body["tool_trace"])
    assert diagnostics["verification_state"] == "verified"


def test_an_abandoned_turn_still_reports_what_it_cost(make_agent):
    """A turn handed to the fallback must not look cheaper than it was.

    Observed live: the model burned seven tool calls over 39 seconds, the guard
    abandoned the turn, and the response reported "0 steps, 1 tool" because the
    retry context started its counters at zero. That inverts the whole point of
    the diagnostics — the most expensive turns looked like the cheapest.
    """
    from datetime import date

    from app.tools.context import ToolContext

    ctx = ToolContext.build("C-100", date(2026, 7, 29))
    ctx.agent_steps = 4
    for name in ("get_order", "list_my_orders", "get_order"):
        ctx.record(name)
    with ctx.timed("agent"):
        pass

    fresh = ctx.for_retry()

    assert fresh.agent_steps == 4, "agent steps from the abandoned rounds were dropped"
    assert fresh.tool_calls == 3, "tool calls from the abandoned rounds were dropped"
    assert fresh.trace == ["get_order", "list_my_orders", "get_order"]
    assert fresh.timings, "per-component timings were dropped"


def test_the_retry_context_still_drops_provisional_escalations(make_agent):
    """Carrying the counters forward must not carry the escalations too."""
    from datetime import date

    from app.tools.context import ToolContext

    ctx = ToolContext.build("C-100", date(2026, 7, 29))
    ctx.add_action("escalated", "ESC-PROVISIONAL", {})
    ctx.add_action("return_created", "RET-DURABLE", {})

    fresh = ctx.for_retry()
    refs = {a["reference"] for a in fresh.actions}

    assert "RET-DURABLE" in refs, "a durable mutation must still be reported"
    assert "ESC-PROVISIONAL" not in refs, "a provisional escalation must not survive"
