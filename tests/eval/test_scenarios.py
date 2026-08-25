"""End-to-end scenario matrix, plus explicit reliability cases.

Scenarios run offline against the deterministic path so the suite is
reproducible and free. That measures the degraded path, which is the stricter
test of the guarantees: whatever the fallback does, the model-driven path is
bounded by the same tool layer.

`grounded_or_escalated` encodes the invariant that actually matters for policy
questions — the reply cites the governing section, or it hands off honestly. It
never invents. Offline, the fallback answers only on a confident retrieval match
and escalates otherwise; with a model in front, that judgement is the model's.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.action_service import reset_action_ledger

pytestmark = pytest.mark.eval

SCENARIOS = json.loads((Path(__file__).parent / "scenarios.json").read_text(encoding="utf-8"))["scenarios"]


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def _run(client: TestClient, scenario: dict) -> dict:
    reset_action_ledger()
    body: dict = {}
    for turn in scenario["turns"]:
        payload = {
            "session_id": f"eval-{scenario['id']}",
            "customer_id": scenario["customer_id"],
            "message": turn,
        }
        if scenario.get("as_of"):
            payload["as_of"] = scenario["as_of"]
        response = client.post("/chat", json=payload)
        assert response.status_code == 200, response.text
        body = response.json()
    return body


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_scenario(client, scenario):
    result = _run(client, scenario)
    expect = scenario["expect"]
    reply = result["message"]
    lowered = reply.lower()
    action_types = [a["type"] for a in result["actions"]]
    label = f"{scenario['id']} ({scenario['name']})"

    for needle in expect.get("reply_contains", []):
        assert needle.lower() in lowered, f"{label}: expected {needle!r} in reply:\n{reply}"

    for needle in expect.get("reply_excludes", []):
        assert needle.lower() not in lowered, f"{label}: {needle!r} must not appear:\n{reply}"

    for tool in expect.get("tools_include", []):
        assert tool in result["tool_trace"], f"{label}: {tool} not called ({result['tool_trace']})"

    if expect.get("tools_empty"):
        assert not result["tool_trace"], f"{label}: expected no tool calls, got {result['tool_trace']}"

    for section in expect.get("policy_sections_include", []):
        assert section in result["policy_sections"], (
            f"{label}: policy section {section} not cited ({result['policy_sections']})"
        )

    if "actions" in expect:
        assert action_types == expect["actions"], f"{label}: actions {action_types}"

    for action in expect.get("actions_include", []):
        assert action in action_types, f"{label}: expected action {action}, got {action_types}"

    for action in expect.get("actions_exclude", []):
        assert action not in action_types, f"{label}: {action} must not happen"

    for needle in expect.get("handoff_contains", []):
        assert needle in (result.get("handoff_summary") or ""), f"{label}: handoff missing {needle!r}"

    if expect.get("active_order"):
        assert expect["active_order"] in reply or expect["active_order"] in str(result["tool_trace"])

    if expect.get("returned_item"):
        created = [a for a in result["actions"] if a["type"] == "return_created"]
        assert created, f"{label}: nothing was created"
        assert created[0]["details"]["item_id"] == expect["returned_item"], (
            f"{label}: created a return for the wrong item: {created[0]['details']}"
        )

    if expect.get("grounded_or_escalated"):
        section = expect["grounded_or_escalated"]
        cited = section in result["policy_sections"]
        escalated = "escalated" in action_types
        assert cited or escalated, (
            f"{label}: neither cited section {section} nor escalated — it may have improvised:\n{reply}"
        )
        if not cited:
            # An honest handoff must actually say it cannot answer.
            assert any(p in lowered for p in ("policy-backed", "human", "team", "colleague")), (
                f"{label}: escalated without telling the customer:\n{reply}"
            )


# --------------------------------------------------------------- reliability


def test_order_lookup_failure_does_not_invent_an_order(client, monkeypatch):
    """A repository failure must not become a confident answer."""
    from app.services.order_repository import OrderRepository

    def broken(self, *_args):
        raise RuntimeError("order service unavailable")

    # Patch the method, not the factory: the tool context resolves its repository
    # through a default_factory that captured the original callable.
    monkeypatch.setattr(OrderRepository, "get_for_customer", broken)
    response = client.post(
        "/chat", json={"session_id": "rel-1", "customer_id": "C-101", "message": "Where is TR-4530?"}
    )
    assert response.status_code == 500
    assert "order service unavailable" not in response.text
    assert "BlueDart" not in response.text


def test_policy_retrieval_failure_escalates_instead_of_improvising(client, monkeypatch):
    from app.services import policy_service

    def boom(_query):
        raise RuntimeError("index unavailable")

    monkeypatch.setattr(policy_service, "get_retriever", lambda: type("R", (), {"search": staticmethod(boom)})())
    body = client.post(
        "/chat",
        json={"session_id": "rel-2", "customer_id": "C-101", "message": "What is your return policy?"},
    ).json()
    assert "escalated" in [a["type"] for a in body["actions"]]
    assert "30 calendar days" not in body["message"], "must not answer from memory"


def test_a_failed_action_is_never_reported_as_success(client, monkeypatch):
    """If the ledger write fails, the customer must not be told it worked."""
    from app.services import action_service

    ledger = action_service.get_action_ledger()
    monkeypatch.setattr(
        ledger, "submit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("returns service down"))
    )
    client.post(
        "/chat", json={"session_id": "rel-3", "customer_id": "C-101", "message": "I want to return TR-4530"}
    )
    response = client.post(
        "/chat", json={"session_id": "rel-3", "customer_id": "C-101", "message": "yes confirm"}
    )
    assert response.status_code == 500
    body = response.json()
    assert "returns service down" not in response.text
    assert "created" not in body.get("detail", "").lower()


def test_an_escalation_failure_is_not_reported_as_a_handoff(client, monkeypatch):
    from app.services import action_service

    ledger = action_service.get_action_ledger()
    monkeypatch.setattr(
        type(ledger),
        "new_case_reference",
        staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("case system down"))),
    )
    response = client.post(
        "/chat", json={"session_id": "rel-4", "customer_id": "C-100", "message": "Do you offer gift wrapping?"}
    )
    assert response.status_code == 500
    assert "case system down" not in response.text
    assert "ESC-" not in response.text
