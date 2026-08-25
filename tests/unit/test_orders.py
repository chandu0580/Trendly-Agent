"""Order loading and the customer boundary."""

from __future__ import annotations

import pytest

from app.services.order_repository import OrderRepository, get_order_repository
from tests.conftest import ORDERS

pytestmark = pytest.mark.unit


def test_the_supplied_dataset_loads_as_provided():
    repo = OrderRepository()
    assert repo.order_count == 10
    for customer, order_id, _ in ORDERS.values():
        assert repo.get_for_customer(order_id, customer) is not None


def test_fixture_authoring_notes_never_leave_the_repository():
    """`_note_for_designers` tells the reader the expected answer — it must not
    reach the model, or the agent would be reading the exam paper."""
    order = get_order_repository().get_for_customer("TR-4527", "C-102")
    serialised = order.model_dump_json()
    assert "_note" not in serialised
    assert "designer" not in serialised.lower()


def test_an_order_is_invisible_to_a_different_customer():
    repo = OrderRepository()
    assert repo.get_for_customer("TR-4530", "C-101") is not None
    assert repo.get_for_customer("TR-4530", "C-100") is None


def test_missing_and_not_yours_are_indistinguishable():
    """The two must be identical, or the reply leaks that an order exists."""
    repo = OrderRepository()
    assert repo.get_for_customer("TR-4530", "C-100") == repo.get_for_customer("TR-9999", "C-100")


def test_unknown_customer_is_rejected():
    repo = OrderRepository()
    assert repo.customer_exists("C-101")
    assert not repo.customer_exists("C-999")


def test_orders_are_returned_by_copy_so_callers_cannot_mutate_the_dataset():
    repo = OrderRepository()
    order = repo.get_for_customer("TR-4530", "C-101")
    order.status = "tampered"
    assert repo.get_for_customer("TR-4530", "C-101").status == "delivered"


def test_item_lookup_is_case_insensitive():
    order = get_order_repository().get_for_customer("TR-4530", "C-101")
    assert order.item("tr-krt-033") is not None
    assert order.item("NOPE-1") is None


def test_partial_shipment_exposes_the_backordered_item():
    order = get_order_repository().get_for_customer("TR-4524", "C-100")
    backordered = order.backordered_items
    assert [i.sku for i in backordered] == ["TR-BLT-005"]
    assert backordered[0].backorder_eta == "2026-08-09"
