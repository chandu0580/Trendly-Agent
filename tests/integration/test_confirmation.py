"""Confirmation before state-changing actions, and only for those.

Read-only work — status, policy, listing, eligibility — must never stop to ask
permission. Mutations always must.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

pytestmark = pytest.mark.integration

MUTATIONS = {"return_created", "exchange_created"}


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def say(client: TestClient, session_id: str, message: str, customer_id: str | None = "C-101") -> dict:
    body = {"session_id": session_id, "message": message}
    if customer_id:
        body["customer_id"] = customer_id
    response = client.post("/chat", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def mutations(body: dict) -> list[str]:
    return [a["type"] for a in body["actions"] if a["type"] in MUTATIONS]


# ------------------------------------------------- read-only work never asks


@pytest.mark.parametrize(
    "customer_id,message",
    [
        ("C-100", "Where is TR-4521?"),
        ("C-101", "How long do refunds take?"),
        ("C-100", "What are my orders?"),
        ("C-101", "Can I return TR-4530?"),
    ],
    ids=["status", "policy", "listing", "eligibility"],
)
def test_read_only_requests_complete_without_a_mutation(client, customer_id, message):
    body = say(client, f"ro-{abs(hash(message)) % 999}", message, customer_id)
    assert not mutations(body)


def test_asking_whether_something_can_be_returned_does_not_return_it(client):
    """The question is not the instruction."""
    body = say(client, "conf-1", "Can I return TR-4530?")
    assert not mutations(body)
    assert "eligible" in body["message"].lower()


# --------------------------------------------------- the two-step protocol


def test_a_return_needs_a_separate_yes(client):
    proposal = say(client, "conf-2", "I want to return TR-4530")
    assert not mutations(proposal)

    done = say(client, "conf-2", "yes, please start it")
    assert mutations(done) == ["return_created"]


def test_an_exchange_needs_a_separate_yes(client):
    proposal = say(client, "conf-3", "Exchange TR-4528 for a size L", "C-103")
    assert not mutations(proposal)

    done = say(client, "conf-3", "yes", "C-103")
    assert mutations(done) == ["exchange_created"]


def test_declining_clears_the_offer_and_changes_nothing(client):
    say(client, "conf-4", "I want to return TR-4530")
    declined = say(client, "conf-4", "No, leave it")
    assert not mutations(declined)

    from app.agent.orchestrator import get_agent

    assert get_agent().sessions.sessions["conf-4"].pending_action is None

    # A later stray "yes" has nothing to land on.
    stray = say(client, "conf-4", "yes")
    assert not mutations(stray)


def test_confirming_twice_still_yields_one_action(client):
    """§8 Scenario A: "yes, do it again" must not create a second return."""
    say(client, "conf-5", "I want to return TR-4530")
    first = say(client, "conf-5", "yes")
    assert mutations(first) == ["return_created"]
    reference = first["actions"][0]["reference"]

    again = say(client, "conf-5", "yes, do it again")
    created = [a for a in again["actions"] if a["type"] == "return_created"]
    assert len(created) <= 1
    if created:
        assert created[0]["reference"] == reference, "a repeat must reuse the first reference"


def test_a_confirmation_executes_only_the_exact_pending_proposal(client):
    """Offered a return on the tee, "yes" must not touch the socks."""
    say(client, "conf-6", "I want to return TR-4522")
    say(client, "conf-6", "the tee")
    done = say(client, "conf-6", "yes")

    created = [a for a in done["actions"] if a["type"] == "return_created"]
    assert created
    assert created[0]["details"]["item_id"] == "TR-TSH-002"


def test_an_action_requiring_a_human_is_escalated_not_confirmed(client):
    """A second exchange needs approval; there is no confirmation that grants it."""
    say(client, "conf-7", "Exchange TR-4528 for size L", "C-103")
    say(client, "conf-7", "yes", "C-103")

    second = say(client, "conf-7", "Actually swap it again for size M", "C-103")
    assert "escalated" in [a["type"] for a in second["actions"]]

    forced = say(client, "conf-7", "yes, do the second exchange", "C-103")
    assert len([a for a in forced["actions"] if a["type"] == "exchange_created"]) == 0
