"""Deterministic eligibility rules — the decisions the model is not allowed to make."""

from __future__ import annotations

from datetime import date

import pytest

from app.services.eligibility import (
    check_delay_credit_eligibility,
    check_exchange_eligibility,
    check_return_eligibility,
)
from app.services.order_repository import get_order_repository
from tests.conftest import AS_OF

pytestmark = pytest.mark.unit


def order(order_id: str, customer_id: str):
    return get_order_repository().get_for_customer(order_id, customer_id)


# ------------------------------------------------------------------- returns


def test_clean_happy_path_is_eligible():
    result = check_return_eligibility(order("TR-4530", "C-101"), "TR-KRT-033", AS_OF)
    assert result.eligible
    assert "2.1" in result.policy_sections
    assert result.deduction == 0


def test_return_outside_thirty_days_is_refused_and_cannot_be_overridden():
    result = check_return_eligibility(order("TR-4523", "C-102"), "TR-JKT-008", AS_OF)
    assert not result.eligible
    assert result.reason_code == "outside_window"
    assert result.policy_sections == ["2.1"]


def test_the_window_is_counted_from_delivery_not_from_order_date():
    # TR-4523 was placed 2026-05-30 and delivered 2026-06-05.
    o = order("TR-4523", "C-102")
    assert check_return_eligibility(o, "TR-JKT-008", date(2026, 7, 5)).eligible
    assert not check_return_eligibility(o, "TR-JKT-008", date(2026, 7, 6)).eligible


def test_exactly_thirty_days_is_still_inside_the_window():
    o = order("TR-4530", "C-101")  # delivered 2026-07-26
    assert check_return_eligibility(o, "TR-KRT-033", date(2026, 8, 25)).eligible
    assert not check_return_eligibility(o, "TR-KRT-033", date(2026, 8, 26)).eligible


def test_jewellery_is_refused_on_category_not_on_date():
    result = check_return_eligibility(order("TR-4527", "C-102"), "TR-EAR-042", AS_OF)
    assert not result.eligible
    assert result.reason_code == "non_returnable_category"
    assert result.policy_sections == ["2.3"]


def test_socks_are_non_returnable_as_innerwear():
    result = check_return_eligibility(order("TR-4522", "C-101"), "TR-SOK-031", AS_OF)
    assert result.reason_code == "non_returnable_category"


def test_final_sale_is_refused_for_refund():
    result = check_return_eligibility(order("TR-4528", "C-103"), "TR-SHR-009", AS_OF)
    assert not result.eligible
    assert result.reason_code == "final_sale_exchange_only"
    assert result.policy_sections == ["2.4"]


def test_cancelled_orders_cannot_have_returns_raised():
    result = check_return_eligibility(order("TR-4529", "C-100"), "TR-SCF-027", AS_OF)
    assert result.reason_code == "cancelled"


def test_a_lost_parcel_is_never_a_return():
    result = check_return_eligibility(order("TR-4526", "C-101"), "TR-BAG-011", AS_OF)
    assert not result.eligible
    assert result.reason_code == "lost_parcel"
    assert result.needs_human
    assert "1.6" in result.policy_sections


def test_undelivered_orders_have_no_return_window_yet():
    result = check_return_eligibility(order("TR-4521", "C-100"), "TR-DRS-014", AS_OF)
    assert result.reason_code == "not_delivered"


def test_footwear_states_the_shoe_box_deduction():
    """TR-4525 is footwear but undelivered, so use a delivered footwear case."""
    o = order("TR-4525", "C-103")
    result = check_return_eligibility(o, "TR-SNK-017", AS_OF)
    assert result.reason_code == "not_delivered"  # cannot return before delivery


def test_damage_reported_in_time_beats_a_non_returnable_category():
    """Policy 6.2 covers non-returnable categories when the item arrives damaged."""
    o = order("TR-4527", "C-102")  # jewellery, delivered 2026-07-23
    result = check_return_eligibility(o, "TR-EAR-042", date(2026, 7, 24), reason="damaged")
    assert result.eligible
    assert "6.2" in result.policy_sections


def test_damage_reported_late_falls_outside_the_48_hour_window():
    o = order("TR-4527", "C-102")
    result = check_return_eligibility(o, "TR-EAR-042", date(2026, 7, 27), reason="damaged")
    assert not result.eligible
    assert result.reason_code == "damage_window_expired"
    assert result.policy_sections == ["6.1"]


def test_shipping_fee_is_only_refunded_when_trendly_is_at_fault():
    o = order("TR-4530", "C-101")
    fault = check_return_eligibility(o, "TR-KRT-033", date(2026, 7, 27), reason="wrong_item")
    mind = check_return_eligibility(o, "TR-KRT-033", AS_OF, reason="change_of_mind")
    assert any("including the original shipping" in c for c in fault.conditions)
    assert any("not refunded for change-of-mind" in c for c in mind.conditions)


def test_an_unknown_item_is_reported_rather_than_guessed():
    result = check_return_eligibility(order("TR-4530", "C-101"), "TR-NOPE-1", AS_OF)
    assert result.reason_code == "item_not_found"


def test_a_second_return_on_the_same_item_is_refused():
    result = check_return_eligibility(
        order("TR-4530", "C-101"), "TR-KRT-033", AS_OF, already_returned=True
    )
    assert result.reason_code == "already_returned"


# ----------------------------------------------------------------- exchanges


def test_size_exchange_is_allowed_inside_the_window():
    result = check_exchange_eligibility(order("TR-4528", "C-103"), "TR-SHR-009", AS_OF)
    assert result.eligible
    assert "4.3" in result.policy_sections


@pytest.mark.parametrize("kind", ["colour", "style"])
def test_colour_and_style_exchanges_are_out_of_scope(kind):
    result = check_exchange_eligibility(order("TR-4528", "C-103"), "TR-SHR-009", AS_OF, kind)
    assert not result.eligible
    assert result.reason_code == "size_only"
    assert result.policy_sections == ["4.1"]


def test_a_second_exchange_needs_human_approval():
    result = check_exchange_eligibility(
        order("TR-4528", "C-103"), "TR-SHR-009", AS_OF, prior_exchanges=1
    )
    assert not result.eligible
    assert result.needs_human
    assert result.policy_sections == ["4.4"]


def test_non_returnable_categories_cannot_be_exchanged_either():
    result = check_exchange_eligibility(order("TR-4527", "C-102"), "TR-EAR-042", AS_OF)
    assert result.reason_code == "non_exchangeable_category"


def test_exchange_window_matches_the_return_window():
    result = check_exchange_eligibility(order("TR-4523", "C-102"), "TR-JKT-008", AS_OF)
    assert result.reason_code == "outside_window"


# -------------------------------------------------------------- delay credit


def test_a_delayed_order_qualifies_for_the_policy_credit():
    result = check_delay_credit_eligibility(order("TR-4525", "C-103"), AS_OF)
    assert result.eligible
    assert result.policy_sections == ["1.5"]


def test_an_on_time_order_does_not_qualify():
    result = check_delay_credit_eligibility(order("TR-4521", "C-100"), AS_OF)
    assert not result.eligible
    assert result.reason_code == "not_delayed"


def test_the_credit_is_once_per_order():
    result = check_delay_credit_eligibility(order("TR-4525", "C-103"), AS_OF, already_issued=True)
    assert result.reason_code == "already_issued"


def test_a_lost_parcel_gets_the_claim_path_not_a_delay_credit():
    result = check_delay_credit_eligibility(order("TR-4526", "C-101"), AS_OF)
    assert not result.eligible
    assert result.reason_code == "lost_parcel"
    assert result.needs_human
