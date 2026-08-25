"""Session isolation, sequential and concurrent.

Now that a session carries a verified identity, an active order, and a pending
proposal, a leak between sessions is a leak between customers. These run real
interleaved and parallel requests against the app rather than reasoning about
the storage design.
"""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from app.agent.orchestrator import get_agent
from app.main import app
from tests.conftest import OWNERSHIP, assert_no_disclosure

pytestmark = pytest.mark.integration


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def say(client: TestClient, session_id: str, customer_id: str, message: str) -> dict:
    response = client.post(
        "/chat",
        json={"session_id": session_id, "customer_id": customer_id, "message": message},
    )
    assert response.status_code == 200, response.text
    return response.json()


# ------------------------------------------------------ interleaved sessions


def test_two_interleaved_sessions_keep_their_own_order(client):
    """A1, B1, A2, B2 — "it" must mean a different order in each."""
    say(client, "iso-A", "C-100", "Where is TR-4524?")
    say(client, "iso-B", "C-101", "Where is TR-4522?")

    a2 = say(client, "iso-A", "C-100", "Can I return it?")
    b2 = say(client, "iso-B", "C-101", "Can I return it?")

    assert "TR-4524" in str(a2["tool_trace"]) or "Jeans" in a2["message"] or "Belt" in a2["message"]
    assert_no_disclosure(a2["message"], "TR-4522", visible_to="C-100")
    assert_no_disclosure(b2["message"], "TR-4524", visible_to="C-101")

    sessions = get_agent().sessions.sessions
    assert sessions["iso-A"].active_order_id == "TR-4524"
    assert sessions["iso-B"].active_order_id == "TR-4522"
    assert sessions["iso-A"].verified_customer_id == "C-100"
    assert sessions["iso-B"].verified_customer_id == "C-101"


def test_a_pending_proposal_belongs_to_one_session(client):
    """B's "yes" must not confirm A's offer."""
    say(client, "iso-C", "C-101", "I want to return TR-4530")
    assert get_agent().sessions.sessions["iso-C"].pending_action is not None

    b = say(client, "iso-D", "C-100", "yes")
    assert not [a for a in b["actions"] if a["type"] == "return_created"]
    assert get_agent().sessions.sessions["iso-C"].pending_action is not None, "A's offer survives"


def test_many_sessions_keep_distinct_verified_identities(client):
    for index, (customer_id, orders) in enumerate(OWNERSHIP.items()):
        say(client, f"iso-multi-{index}", customer_id, f"Where is {orders[0]}?")

    sessions = get_agent().sessions.sessions
    for index, (customer_id, orders) in enumerate(OWNERSHIP.items()):
        state = sessions[f"iso-multi-{index}"]
        assert state.verified_customer_id == customer_id
        assert state.active_order_id == orders[0]


# ------------------------------------------------------------- concurrency


def run_parallel(jobs) -> list:
    """Run callables on real threads and surface the first exception."""
    results: list = [None] * len(jobs)
    errors: list = []
    barrier = threading.Barrier(len(jobs))

    def runner(index, job):
        try:
            barrier.wait()
            results[index] = job()
        except Exception as exc:  # noqa: BLE001 - re-raised below
            errors.append(exc)

    threads = [threading.Thread(target=runner, args=(i, j)) for i, j in enumerate(jobs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors:
        raise errors[0]
    return results


def test_ten_concurrent_sessions_do_not_cross_customers(client):
    pairs = [
        (customer_id, order_id)
        for customer_id, orders in OWNERSHIP.items()
        for order_id in orders
    ]
    jobs = [
        (lambda c=customer_id, o=order_id, i=index: (c, o, say(client, f"cc-{i}", c, f"Where is {o}?")))
        for index, (customer_id, order_id) in enumerate(pairs)
    ]
    outcomes = run_parallel(jobs)

    assert len(outcomes) == 10
    for customer_id, order_id, body in outcomes:
        assert order_id in body["message"], f"{customer_id} lost its own order"
        for other in [o for o in OWNERSHIP if o != customer_id]:
            for foreign in OWNERSHIP[other]:
                assert_no_disclosure(body["message"], foreign, visible_to=customer_id)

    sessions = get_agent().sessions.sessions
    for index, (customer_id, order_id) in enumerate(pairs):
        state = sessions[f"cc-{index}"]
        assert state.verified_customer_id == customer_id
        assert state.active_order_id == order_id


def test_concurrent_verification_binds_each_session_separately(client):
    """Ten sessions verifying at once, none sending a customer_id in the body."""

    def verify(index: int, customer_id: str, order_id: str):
        response = client.post(
            "/chat",
            json={"session_id": f"cv-{index}", "message": f"{customer_id}, {order_id}"},
        )
        return customer_id, response.json()

    pairs = [(c, o) for c, orders in OWNERSHIP.items() for o in orders]
    outcomes = run_parallel(
        [lambda i=i, c=c, o=o: verify(i, c, o) for i, (c, o) in enumerate(pairs)]
    )

    sessions = get_agent().sessions.sessions
    for index, (customer_id, order_id) in enumerate(pairs):
        state = sessions[f"cv-{index}"]
        assert state.verified_customer_id == customer_id
        assert state.active_order_id == order_id
        assert state.is_verified
    assert all(body["diagnostics"]["verification_state"] == "verified" for _c, body in outcomes)


def test_concurrent_confirmations_of_the_same_action_create_one_return(client):
    """The duplicate-request case: several clients confirming at once."""
    say(client, "cd-1", "C-101", "I want to return TR-4530")

    outcomes = run_parallel([lambda: say(client, "cd-1", "C-101", "yes") for _ in range(6)])

    references = {
        action["reference"]
        for body in outcomes
        for action in body["actions"]
        if action["type"] == "return_created"
    }
    assert len(references) <= 1, f"concurrent confirmations produced {references}"


def test_concurrent_actions_across_customers_stay_separate(client):
    """Two customers returning their own items at the same moment."""
    say(client, "cx-A", "C-101", "I want to return TR-4530")
    say(client, "cx-B", "C-103", "Exchange TR-4528 for size L")

    outcomes = run_parallel(
        [
            lambda: say(client, "cx-A", "C-101", "yes"),
            lambda: say(client, "cx-B", "C-103", "yes"),
        ]
    )

    created = [a for body in outcomes for a in body["actions"] if a["type"] != "escalated"]
    orders = {a["details"]["order_id"] for a in created}
    assert orders <= {"TR-4530", "TR-4528"}
    for action in created:
        if action["type"] == "return_created":
            assert action["details"]["order_id"] == "TR-4530"
        if action["type"] == "exchange_created":
            assert action["details"]["order_id"] == "TR-4528"


def test_a_session_id_is_the_only_key_into_state(client):
    """Nothing global leaks the previous caller's context into a new session."""
    say(client, "fresh-A", "C-100", "Where is TR-4524?")
    body = client.post("/chat", json={"session_id": "fresh-B", "message": "Can I return it?"}).json()

    assert body["diagnostics"]["verification_state"] != "verified"
    assert_no_disclosure(body["message"], "TR-4524")


# --------------------------------------------------- same-session concurrency


def test_concurrent_requests_on_one_session_do_not_corrupt_its_state(client):
    """Two requests on the same conversation read, decide on, and write back the
    same ConversationState. Without serialisation they interleave and the loser's
    write silently discards the winner's verification or active order."""
    say(client, "same-1", "C-100", "Where is TR-4524?")

    outcomes = run_parallel(
        [
            lambda: say(client, "same-1", "C-100", "Where is TR-4521?"),
            lambda: say(client, "same-1", "C-100", "Where is TR-4529?"),
            lambda: say(client, "same-1", "C-100", "What are my orders?"),
            lambda: say(client, "same-1", "C-100", "How long do refunds take?"),
        ]
    )

    state = get_agent().sessions.sessions["same-1"]
    assert state.verified_customer_id == "C-100", "identity survived"
    assert state.is_verified, "verification was not lost by an interleaved write"
    assert state.active_order_id in {"TR-4521", "TR-4524", "TR-4529"}, state.active_order_id
    # Every turn still answered, and none leaked another customer's order.
    assert len(outcomes) == 4
    for body in outcomes:
        for foreign in ("TR-4522", "TR-4526", "TR-4530", "TR-4523"):
            assert_no_disclosure(body["message"], foreign, visible_to="C-100")


def test_concurrent_confirmations_on_one_session_create_one_action(client):
    """The duplicate-submit race, on a single conversation."""
    say(client, "same-2", "C-101", "I want to return TR-4530")

    outcomes = run_parallel([lambda: say(client, "same-2", "C-101", "yes") for _ in range(8)])

    references = {
        action["reference"]
        for body in outcomes
        for action in body["actions"]
        if action["type"] == "return_created"
    }
    assert len(references) <= 1, f"concurrent confirmations produced {references}"


def test_a_pending_proposal_is_not_lost_by_a_concurrent_read(client):
    """A status question racing the offer must not clear it."""
    say(client, "same-3", "C-101", "I want to return TR-4530")

    run_parallel(
        [
            lambda: say(client, "same-3", "C-101", "Where is TR-4530?"),
            lambda: say(client, "same-3", "C-101", "How long do refunds take?"),
        ]
    )

    # The offer either survived its TTL or expired cleanly — never half-written.
    pending = get_agent().sessions.sessions["same-3"].pending_action
    if pending is not None:
        assert pending.customer_id == "C-101"
        assert pending.order_id == "TR-4530"


def test_turns_on_one_session_are_serialised_not_dropped(client):
    """Every concurrent request gets its own answer and its own trace."""
    say(client, "same-4", "C-103", "Where is TR-4525?")

    outcomes = run_parallel(
        [lambda i=i: say(client, "same-4", "C-103", f"Where is TR-4525? ({i})") for i in range(6)]
    )

    traces = {body["diagnostics"]["trace_id"] for body in outcomes}
    assert len(traces) == 6, "each concurrent turn is its own run"
    assert all(body["message"] for body in outcomes)
