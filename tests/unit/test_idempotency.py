"""Duplicate action handling.

A retry that produces a second reference is indistinguishable from a duplicate
refund, so the identity of an action is `(customer, kind, order, item)` and the
second submission returns the first result.
"""

from __future__ import annotations

import threading

import pytest

from app.models.conversation import PendingAction
from app.services.action_service import ActionLedger, get_action_ledger
from app.tools import build_toolset
from app.tools.context import ToolContext
from tests.conftest import AS_OF

pytestmark = pytest.mark.unit

KURTA = {"order_id": "TR-4530", "item_id": "TR-KRT-033"}


def confirmed_tools(customer_id="C-101", item_id="TR-KRT-033", kind="return"):
    """A context that has already passed verification, eligibility, and confirmation."""
    pending = PendingAction(
        customer_id=customer_id, kind=kind, order_id="TR-4530", item_id=item_id
    )
    ctx = ToolContext.build(customer_id, AS_OF, user_confirmed=True, pending=pending)
    tools = {t.name: t for t in build_toolset(ctx)}
    tools["get_order"].invoke({"order_id": "TR-4530"})
    check = "check_return_eligibility" if kind == "return" else "check_exchange_eligibility"
    tools[check].invoke({"order_id": "TR-4530", "item_id": item_id})
    return tools, ctx


# ------------------------------------------------------------- the ledger


def test_the_ledger_returns_the_first_record_on_a_repeat():
    ledger = ActionLedger()
    first, created_first = ledger.submit("C-101", "return", "TR-4530", "A", "RET", {"n": 1})
    second, created_second = ledger.submit("C-101", "return", "TR-4530", "A", "RET", {"n": 2})

    assert created_first is True and created_second is False
    assert first.reference == second.reference
    assert second.detail == {"n": 1}, "the original record must not be overwritten"


@pytest.mark.parametrize(
    "customer,kind,order,item",
    [
        ("C-100", "return", "TR-4530", "A"),
        ("C-101", "exchange", "TR-4530", "A"),
        ("C-101", "return", "TR-4522", "A"),
        ("C-101", "return", "TR-4530", "B"),
    ],
    ids=["other-customer", "other-kind", "other-order", "other-item"],
)
def test_each_part_of_the_key_makes_a_different_action(customer, kind, order, item):
    ledger = ActionLedger()
    base, _ = ledger.submit("C-101", "return", "TR-4530", "A", "RET", {})
    other, created = ledger.submit(customer, kind, order, item, "RET", {})
    assert created is True
    assert other.reference != base.reference


def test_order_and_item_ids_are_matched_case_insensitively():
    ledger = ActionLedger()
    first, _ = ledger.submit("C-101", "return", "TR-4530", "TR-KRT-033", "RET", {})
    second, created = ledger.submit("C-101", "return", "tr-4530", "tr-krt-033", "RET", {})
    assert created is False and second.reference == first.reference


@pytest.mark.parametrize("kind,prefix", [("return", "RET"), ("exchange", "EXC")])
def test_concurrent_submissions_create_exactly_one_action(kind, prefix):
    """Without the lock, two threads can both see 'no record' and both create one.

    Run for both action kinds: the ledger is kind-agnostic by design, and this is
    the assertion that keeps it that way if the two paths ever diverge.
    """
    ledger = ActionLedger()
    results: list[tuple] = []
    barrier = threading.Barrier(12)

    def submit():
        barrier.wait()
        results.append(ledger.submit("C-101", kind, "TR-4530", "A", prefix, {}))

    threads = [threading.Thread(target=submit) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 12
    assert sum(1 for _record, created in results if created) == 1
    assert len({record.reference for record, _ in results}) == 1


# ------------------------------------------------------------ through the tool


def test_a_repeated_return_returns_the_original_reference():
    tools, ctx = confirmed_tools()
    first = tools["initiate_return"].invoke(KURTA)

    tools, ctx = confirmed_tools()
    second = tools["initiate_return"].invoke(KURTA)

    assert first["reference"] == second["reference"]
    assert second["detail"]["replayed"] is True
    assert "already exists" in second["message"]


def test_a_replay_does_not_need_a_fresh_proposal():
    """A retry after a timeout has no live proposal, but the action already happened."""
    tools, _ = confirmed_tools()
    first = tools["initiate_return"].invoke(KURTA)

    ctx = ToolContext.build("C-101", AS_OF)  # no confirmation, no grant
    replay = {t.name: t for t in build_toolset(ctx)}["initiate_return"].invoke(KURTA)

    assert replay["created"] is True
    assert replay["reference"] == first["reference"]
    assert replay["detail"]["replayed"] is True


def test_a_replay_cannot_be_used_to_read_another_customers_action():
    """The key includes the customer, so C-100 gets no sight of C-101's return."""
    tools, _ = confirmed_tools(customer_id="C-101")
    tools["initiate_return"].invoke(KURTA)

    ctx = ToolContext.build("C-100", AS_OF)
    result = {t.name: t for t in build_toolset(ctx)}["initiate_return"].invoke(KURTA)
    assert result["created"] is False
    assert not ctx.actions


def test_a_refused_action_never_enters_the_registry():
    """Only executed actions are replayable; a denial must leave no trace."""
    ledger = get_action_ledger()
    ctx = ToolContext.build("C-100", AS_OF)  # TR-4530 belongs to C-101
    tools = {t.name: t for t in build_toolset(ctx)}
    tools["get_order"].invoke({"order_id": "TR-4530"})
    assert tools["initiate_return"].invoke(KURTA)["created"] is False
    assert ledger.find("C-100", "return", "TR-4530", "TR-KRT-033") is None


def test_a_delay_credit_is_idempotent_per_order():
    ctx = ToolContext.build("C-103", AS_OF)
    tools = {t.name: t for t in build_toolset(ctx)}
    tools["get_order"].invoke({"order_id": "TR-4525"})
    first = tools["issue_delay_credit"].invoke({"order_id": "TR-4525"})

    ctx2 = ToolContext.build("C-103", AS_OF)
    tools2 = {t.name: t for t in build_toolset(ctx2)}
    tools2["get_order"].invoke({"order_id": "TR-4525"})
    second = tools2["issue_delay_credit"].invoke({"order_id": "TR-4525"})

    assert first["created"] is True
    # The second is refused by the policy check before it reaches the ledger.
    assert second["created"] is False
    assert second["detail"]["reason_code"] == "already_issued"


def test_idempotency_does_not_weaken_proposal_binding():
    """A replay is scoped to the exact action; it cannot spill onto a sibling item."""
    tools, _ = confirmed_tools(item_id="TR-KRT-033")
    tools["initiate_return"].invoke(KURTA)

    ctx = ToolContext.build("C-101", AS_OF)
    other = {t.name: t for t in build_toolset(ctx)}["initiate_return"].invoke(
        {"order_id": "TR-4530", "item_id": "TR-OTHER-1"}
    )
    assert other["created"] is False
