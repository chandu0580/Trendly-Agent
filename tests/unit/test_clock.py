"""Controlled clock and the exact boundaries of every time-sensitive rule.

Nothing here reads the machine clock, so none of it can pass in the morning and
fail in the evening.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.services.clock import (
    IST,
    FixedClock,
    SystemClock,
    as_ist_instant,
    get_clock,
    reset_clock,
    set_clock,
    to_ist_date,
    to_ist_datetime,
)
from app.services.eligibility import (
    check_delay_credit_eligibility,
    check_exchange_eligibility,
    check_return_eligibility,
)
from app.services.order_repository import get_order_repository

pytestmark = pytest.mark.unit


def order(order_id: str, customer_id: str):
    return get_order_repository().get_for_customer(order_id, customer_id)


# ------------------------------------------------------------------ the clock


def test_a_fixed_clock_does_not_move():
    clock = FixedClock.at("2026-07-29T10:00:00+05:30")
    assert clock.today() == date(2026, 7, 29)
    assert clock.today() == clock.today()


def test_the_installed_clock_can_be_swapped_and_restored():
    original = get_clock()
    try:
        set_clock(FixedClock.at("2026-01-02"))
        assert get_clock().today() == date(2026, 1, 2)
    finally:
        set_clock(original)
    reset_clock()
    assert isinstance(get_clock(), SystemClock)


# -------------------------------------------------------------- the timezone


def test_a_utc_timestamp_is_read_on_the_ist_calendar():
    """20:00 UTC is already the next day in IST. Truncating the UTC string would
    put the delivery a day early, at exactly the boundary that matters."""
    assert to_ist_date("2026-07-26T20:00:00Z") == date(2026, 7, 27)
    assert to_ist_date("2026-07-26T18:29:00Z") == date(2026, 7, 26)
    assert to_ist_date("2026-07-26T18:31:00Z") == date(2026, 7, 27)


def test_a_bare_date_is_treated_as_ist_midnight():
    """`expected_delivery` is already a customer-facing IST date."""
    assert to_ist_datetime("2026-07-31") == datetime(2026, 7, 31, tzinfo=IST)


def test_a_bare_request_date_becomes_start_of_day():
    """The earliest moment it could have been, so a window stays open as long as
    the date allows rather than expiring against the customer."""
    instant = as_ist_instant(date(2026, 7, 29))
    assert instant.tzinfo == IST
    assert (instant.hour, instant.minute) == (0, 0)


def test_no_dataset_delivery_date_shifts_under_conversion():
    """Guards the migration: correct IST handling must not silently move a window."""
    repo = get_order_repository()
    for customer_id, order_ids in {
        "C-100": ["TR-4521", "TR-4524", "TR-4529"],
        "C-101": ["TR-4522", "TR-4526", "TR-4530"],
        "C-102": ["TR-4523", "TR-4527"],
        "C-103": ["TR-4525", "TR-4528"],
    }.items():
        for order_id in order_ids:
            record = repo.get_for_customer(order_id, customer_id)
            if record.delivered_at:
                naive = date.fromisoformat(record.delivered_at[:10])
                assert to_ist_date(record.delivered_at) == naive, order_id


# ------------------------------------------------- 30-day return window


DELIVERED = date(2026, 7, 26)  # TR-4530, IST


@pytest.mark.parametrize(
    "days,expected",
    [(29, True), (30, True), (31, False)],
    ids=["day-29-inside", "day-30-inside", "day-31-outside"],
)
def test_the_return_window_boundary(days, expected):
    result = check_return_eligibility(
        order("TR-4530", "C-101"), "TR-KRT-033", DELIVERED + timedelta(days=days)
    )
    assert result.eligible is expected
    if not expected:
        assert result.reason_code == "outside_window"


@pytest.mark.parametrize("days,expected", [(29, True), (30, True), (31, False)])
def test_the_exchange_window_uses_the_same_boundary(days, expected):
    result = check_exchange_eligibility(
        order("TR-4528", "C-103"), "TR-SHR-009", date(2026, 7, 19) + timedelta(days=days)
    )
    assert result.eligible is expected


# ---------------------------------------------------- 48-hour damage window


DELIVERED_AT = datetime(2026, 7, 23, 12, 10, tzinfo=timezone.utc)  # TR-4527


@pytest.mark.parametrize(
    "hours,expected",
    [(47, True), (48, True), (49, False)],
    ids=["47h-inside", "48h-exactly-inside", "49h-outside"],
)
def test_the_damage_report_window_boundary(hours, expected):
    """Measured in real hours, not whole days."""
    result = check_return_eligibility(
        order("TR-4527", "C-102"),
        "TR-EAR-042",
        DELIVERED_AT + timedelta(hours=hours),
        reason="damaged",
    )
    assert result.eligible is expected, f"{hours}h -> {result.reason_code}"
    if not expected:
        assert result.reason_code == "damage_window_expired"


def test_a_date_granular_request_resolves_the_damage_boundary_in_the_customers_favour():
    """A caller with no time of day gets start-of-day, so day two still qualifies."""
    delivered_day = date(2026, 7, 23)
    assert check_return_eligibility(
        order("TR-4527", "C-102"), "TR-EAR-042", delivered_day + timedelta(days=2), reason="damaged"
    ).eligible is True
    assert check_return_eligibility(
        order("TR-4527", "C-102"), "TR-EAR-042", delivered_day + timedelta(days=3), reason="damaged"
    ).eligible is False


# --------------------------------------------------------- delay threshold


def test_the_delay_threshold_boundary():
    """TR-4521 is expected 2026-07-31 and is not flagged delayed in the data."""
    expected = date(2026, 7, 31)
    on_the_line = check_delay_credit_eligibility(order("TR-4521", "C-100"), expected + timedelta(days=3))
    past_it = check_delay_credit_eligibility(order("TR-4521", "C-100"), expected + timedelta(days=4))
    assert on_the_line.eligible is False
    assert on_the_line.reason_code == "not_delayed"
    assert past_it.eligible is True


def test_an_order_flagged_delayed_qualifies_regardless_of_arithmetic():
    """The dataset's own status is authoritative when it says delayed."""
    result = check_delay_credit_eligibility(order("TR-4525", "C-103"), date(2026, 7, 16))
    assert result.eligible is True


# ------------------------------------------------------------- determinism


def test_rules_accept_both_a_date_and_a_datetime():
    as_date = check_return_eligibility(order("TR-4530", "C-101"), "TR-KRT-033", date(2026, 8, 20))
    as_instant = check_return_eligibility(
        order("TR-4530", "C-101"), "TR-KRT-033", datetime(2026, 8, 20, 9, 0, tzinfo=IST)
    )
    assert as_date.eligible == as_instant.eligible is True


def test_the_same_inputs_always_give_the_same_verdict():
    verdicts = {
        check_return_eligibility(
            order("TR-4530", "C-101"), "TR-KRT-033", date(2026, 8, 25)
        ).reason_code
        for _ in range(20)
    }
    assert len(verdicts) == 1
