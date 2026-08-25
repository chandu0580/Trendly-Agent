"""HTTP contract tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

pytestmark = pytest.mark.integration


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def post(client: TestClient, session_id: str, message: str, **kwargs) -> dict:
    body = {"session_id": session_id, "message": message, **kwargs}
    response = client.post("/chat", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_health_reports_what_is_loaded(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["orders_loaded"] == 10
    assert body["policy_sections_indexed"] >= 25


def test_chat_returns_the_documented_contract(client):
    body = post(client, "api-1", "Where is TR-4521?", customer_id="C-100")
    assert set(body) >= {"session_id", "message", "status", "actions", "tool_trace", "mode"}
    assert body["session_id"] == "api-1"
    assert isinstance(body["message"], str) and body["message"]


def test_customer_id_is_optional_and_defaults_to_a_demo_identity(client):
    """The documented {session_id, message} body must work on its own."""
    body = post(client, "api-2", "hello")
    assert body["status"] in {"completed", "escalated"}


def test_the_tool_trace_shows_the_work(client):
    body = post(client, "api-3", "I want to return TR-4530", customer_id="C-101")
    assert body["tool_trace"] == ["get_order", "check_return_eligibility"]
    assert "2.1" in body["policy_sections"]


def test_actions_are_auditable(client):
    post(client, "api-4", "I want to return TR-4530", customer_id="C-101")
    body = post(client, "api-4", "yes confirm", customer_id="C-101")
    assert [a["type"] for a in body["actions"]] == ["return_created"]
    assert body["actions"][0]["reference"].startswith("RET-")


def test_escalation_exposes_a_handoff_summary(client):
    body = post(client, "api-5", "TR-4526 is lost, refund it", customer_id="C-101")
    assert body["status"] == "escalated"
    assert body["handoff_summary"]
    assert "Lost-parcel claim" in body["handoff_summary"]


def test_a_bad_as_of_is_rejected_without_leaking_internals(client):
    response = client.post(
        "/chat", json={"session_id": "api-6", "message": "hi", "as_of": "not-a-date"}
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "invalid_as_of"
    assert "Traceback" not in response.text


def test_validation_rejects_an_empty_message(client):
    response = client.post("/chat", json={"session_id": "api-7", "message": ""})
    assert response.status_code == 422


def test_the_v1_alias_still_works(client):
    """An already-published demo URL must not break."""
    response = client.post(
        "/v1/chat", json={"session_id": "api-8", "message": "Where is TR-4521?", "customer_id": "C-100"}
    )
    assert response.status_code == 200
    assert "TR-4521" in response.json()["message"]


def test_an_unhandled_error_returns_a_safe_message(client, monkeypatch):
    def boom(**_kwargs):
        raise RuntimeError("internal detail that must not leak")

    monkeypatch.setattr("app.main.get_agent", lambda: type("A", (), {"respond": staticmethod(boom)})())
    response = client.post("/chat", json={"session_id": "api-9", "message": "hi"})
    assert response.status_code == 500
    assert "internal detail" not in response.text
    assert response.json()["error"] == "internal_error"


def test_pages_render(client):
    for path in ("/", "/agent"):
        assert client.get(path).status_code == 200


def test_openapi_documents_the_action_types(client):
    schema = client.get("/openapi.json").json()
    enum = schema["components"]["schemas"]["Action"]["properties"]["type"]["enum"]
    assert set(enum) == {"return_created", "exchange_created", "credit_issued", "escalated"}
