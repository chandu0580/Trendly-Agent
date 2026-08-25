"""Deterministic eligibility rules.

These are pure functions over verified order data. The model never computes an
eligibility verdict: date arithmetic and category exclusions are exactly what an
LLM gets subtly wrong, and "refused for the wrong reason" is a defensible-sounding
answer that is still wrong. Retrieval supplies the evidence; this decides.

Every result names the policy sections it applied so the reply can cite them.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from ..models.order import NON_RETURNABLE_CATEGORIES, Order
from ..models.tool_results import EligibilityResult
from .clock import as_ist_instant, to_ist_date, to_ist_datetime

RETURN_WINDOW_DAYS = 30
DAMAGE_REPORT_HOURS = 48  # policy 6.1
DELAY_THRESHOLD_DAYS = 3
DELAY_CREDIT_AMOUNT = 250
FOOTWEAR_NO_BOX_DEDUCTION = 300
TRENDLY_FAULT_REASONS = frozenset({"damaged", "defective", "wrong_item"})
RETURN_REASONS = ("change_of_mind", "damaged", "defective", "wrong_item")
EXCHANGE_KINDS = ("size", "colour", "style")

# Policy 3.1. The dataset's `prepaid_card` is a card for refund purposes, so it
# shares the card row rather than being a fourth destination.
INSPECTION_DAYS = "2-3 business days"
REFUND_TIMINGS: dict[str, dict] = {
    "credit_card": {"destination": "the original card", "window": "5-7 business days"},
    "prepaid_card": {"destination": "the original card", "window": "5-7 business days"},
    "upi": {"destination": "the original UPI ID", "window": "3-5 business days"},
    "cash_on_delivery": {
        "destination": "bank transfer or store credit",
        "window": "7-10 business days",
        # Policy 3.3: COD needs bank details, which only a human may collect.
        "requires_human": True,
    },
    "store_credit": {"destination": "store credit", "window": "immediate"},
}

FREE_SHIPPING_THRESHOLD = 1499
STANDARD_SHIPPING_FEE = 99
EXPRESS_SHIPPING_FEE = 199
SHIPPING_MODES = ("standard", "express")


def get_refund_timing(order: Order) -> dict:
    """Policy 3.1 resolved against this order's actual payment method.

    The mapping is the whole point: a customer asking "when do I get my money
    back?" wants their answer, not the table. Doing it here rather than letting
    the model read the table off a retrieved passage is what keeps the answer
    from drifting between payment methods.
    """
    method = (order.payment_method or "").strip().lower()
    timing = REFUND_TIMINGS.get(method)

    if timing is None:
        return {
            "known": False,
            "payment_method": order.payment_method,
            "reason_code": "unknown_payment_method",
            "policy_sections": ["3.1"],
            "requires_human": True,
            "next_action": "Escalate: the policy does not describe a refund route for this payment method.",
        }

    requires_human = bool(timing.get("requires_human"))
    result = {
        "known": True,
        "payment_method": method,
        "refund_destination": timing["destination"],
        "refund_window": timing["window"],
        "inspection_window": INSPECTION_DAYS,
        "reason_code": "cod_requires_human" if requires_human else "timing_resolved",
        "policy_sections": ["3.1", "3.3"] if requires_human else ["3.1"],
        "requires_human": requires_human,
    }
    result["next_action"] = (
        "Escalate so a human can collect bank details over Trendly's secure link. "
        "Never ask the customer for bank or card details in chat."
        if requires_human
        else f"Tell the customer the refund goes to {timing['destination']} and takes "
        f"{timing['window']} after inspection ({INSPECTION_DAYS})."
    )
    return result


def quote_shipping_fee(
    order_total: float, shipping_mode: str = "standard", payment_method: str | None = None
) -> dict:
    """Policy 1.3, resolved against an order's total and payment method."""
    mode = (shipping_mode or "standard").strip().lower()
    if mode not in SHIPPING_MODES:
        return {
            "available": False,
            "reason_code": "unknown_mode",
            "reason": f"Trendly offers {' and '.join(SHIPPING_MODES)} shipping only.",
            "policy_sections": ["1.3"],
        }

    if mode == "express":
        if (payment_method or "").strip().lower() == "cash_on_delivery":
            return {
                "available": False,
                "shipping_mode": "express",
                "reason_code": "express_unavailable_for_cod",
                "reason": "Express shipping is not available for cash-on-delivery orders.",
                "policy_sections": ["1.3"],
            }
        return {
            "available": True,
            "shipping_mode": "express",
            "fee": float(EXPRESS_SHIPPING_FEE),
            "currency": "INR",
            "reason_code": "express_flat_fee",
            "reason": f"Express shipping is a flat Rs {EXPRESS_SHIPPING_FEE}.",
            "policy_sections": ["1.3"],
        }

    free = float(order_total or 0) >= FREE_SHIPPING_THRESHOLD
    return {
        "available": True,
        "shipping_mode": "standard",
        "fee": 0.0 if free else float(STANDARD_SHIPPING_FEE),
        "currency": "INR",
        "reason_code": "free_over_threshold" if free else "below_free_threshold",
        "reason": (
            f"Standard shipping is free on orders of Rs {FREE_SHIPPING_THRESHOLD} and above."
            if free
            else f"Orders below Rs {FREE_SHIPPING_THRESHOLD} carry a flat Rs {STANDARD_SHIPPING_FEE} shipping fee."
        ),
        "policy_sections": ["1.3"],
    }


def _delivered_on(order: Order) -> date | None:
    """Delivery date on the customer's calendar, not the UTC one."""
    return to_ist_date(order.delivered_at)


def _request_day(request_date: date | datetime) -> date:
    """The customer-calendar day a request was made on.

    Day-based windows (30-day return, 3-day delay) are judged by date, so a
    datetime request is reduced to its IST date rather than compared by clock time.
    """
    if isinstance(request_date, datetime):
        return as_ist_instant(request_date).date()
    return request_date


def _damage_window_expired(order: Order, request_date: date | datetime) -> bool:
    """Policy 6.1, measured in real hours where the request clock allows it.

    A request that carries only a date is normalised to end-of-day IST, so a
    date-granular caller never expires the window early. That boundary resolves
    in the customer's favour by design.
    """
    delivered = to_ist_datetime(order.delivered_at)
    asked = as_ist_instant(request_date)
    if delivered is None or asked is None:
        return False
    return asked > delivered + timedelta(hours=DAMAGE_REPORT_HOURS)


def check_return_eligibility(
    order: Order,
    item_id: str,
    request_date: date | datetime,
    reason: str = "change_of_mind",
    already_returned: bool = False,
) -> EligibilityResult:
    """Evaluate a return against policy sections 2.x, 3.2 and 6.x."""
    item = order.item(item_id)
    if not item:
        return EligibilityResult(
            reason_code="item_not_found",
            reason="That item is not on this order.",
            next_action="Ask the customer which item they mean.",
        )

    if reason not in RETURN_REASONS:
        reason = "change_of_mind"

    if already_returned:
        return EligibilityResult(
            reason_code="already_returned",
            reason="A return has already been raised for this item.",
            next_action="Tell the customer the existing return is already in progress.",
        )

    if order.status == "cancelled":
        return EligibilityResult(
            reason_code="cancelled",
            reason="Cancelled orders cannot have returns raised against them.",
            policy_sections=["2.6"],
            next_action="Explain the cancellation refund path instead.",
        )

    if order.status == "lost_in_transit":
        return EligibilityResult(
            reason_code="lost_parcel",
            reason="This parcel is marked lost, which is a lost-parcel claim rather than a return.",
            policy_sections=["1.6"],
            needs_human=True,
            next_action="Escalate as a lost-parcel claim; do not raise a return.",
        )

    delivered = _delivered_on(order)
    if not delivered:
        return EligibilityResult(
            reason_code="not_delivered",
            reason="A return can be raised only after the order has been delivered.",
            policy_sections=["2.1"],
            next_action="Explain that the return window starts at delivery.",
        )

    if _request_day(request_date) > delivered + timedelta(days=RETURN_WINDOW_DAYS):
        return EligibilityResult(
            reason_code="outside_window",
            reason=(
                f"The {RETURN_WINDOW_DAYS}-calendar-day return window closed on "
                f"{delivered + timedelta(days=RETURN_WINDOW_DAYS)}."
            ),
            policy_sections=["2.1"],
            next_action="Explain that the window has expired; it cannot be overridden.",
        )

    # Policy 6.2: a damaged, defective, or wrong item is covered even in an
    # otherwise non-returnable category — so this check runs before 2.3.
    trendly_fault = reason in TRENDLY_FAULT_REASONS
    if trendly_fault and _damage_window_expired(order, request_date):
        return EligibilityResult(
            reason_code="damage_window_expired",
            reason="Damaged, defective, or wrong items must be reported within 48 hours of delivery, with photos.",
            policy_sections=["6.1"],
            next_action="Offer a human review; the automatic path has closed.",
        )

    if item.category in NON_RETURNABLE_CATEGORIES and not trendly_fault:
        return EligibilityResult(
            reason_code="non_returnable_category",
            reason=f"{item.name} is in a non-returnable category for hygiene and safety reasons.",
            policy_sections=["2.3"],
            next_action="Mention that a damaged, defective, or wrong item would qualify instead.",
        )

    if item.final_sale and not trendly_fault:
        return EligibilityResult(
            reason_code="final_sale_exchange_only",
            reason="Final-sale items are eligible for a size exchange only — no refund or store credit.",
            policy_sections=["2.4"],
            next_action="Offer to check a size exchange instead.",
        )

    conditions = ["Unworn, unwashed, with original tags and packaging where provided."]
    sections = ["2.1", "2.2"]
    deduction = 0.0

    if item.category == "footwear":
        conditions.append(
            f"Return in the original shoe box; without it a ₹{FOOTWEAR_NO_BOX_DEDUCTION:.0f} deduction applies."
        )
        sections.append("2.5")
        deduction = float(FOOTWEAR_NO_BOX_DEDUCTION)

    if trendly_fault:
        conditions.append("Photographs are required.")
        conditions.append("Replacement or full refund including the original shipping fee.")
        sections += ["3.2", "6.2"]
    else:
        conditions.append("The original ₹99 shipping fee is not refunded for change-of-mind returns.")
        sections.append("3.2")

    return EligibilityResult(
        eligible=True,
        reason_code="eligible",
        reason=f"{item.name} meets the return rules.",
        deduction=deduction,
        conditions=conditions,
        policy_sections=sections,
        next_action="Summarise the conditions and ask the customer to confirm before creating anything.",
    )


def check_exchange_eligibility(
    order: Order,
    item_id: str,
    request_date: date | datetime,
    exchange_kind: str = "size",
    prior_exchanges: int = 0,
) -> EligibilityResult:
    """Evaluate an exchange against policy section 4.x."""
    item = order.item(item_id)
    if not item:
        return EligibilityResult(
            reason_code="item_not_found",
            reason="That item is not on this order.",
            next_action="Ask the customer which item they mean.",
        )

    if exchange_kind not in EXCHANGE_KINDS:
        exchange_kind = "size"

    if exchange_kind != "size":
        return EligibilityResult(
            reason_code="size_only",
            reason="Trendly offers size exchanges only. A colour or style change means returning the item and placing a new order.",
            policy_sections=["4.1"],
            next_action="Offer the return-and-reorder path instead.",
        )

    if order.status == "cancelled":
        return EligibilityResult(
            reason_code="cancelled",
            reason="Cancelled orders cannot be exchanged.",
            policy_sections=["2.6"],
            next_action="Explain the cancellation refund path.",
        )

    delivered = _delivered_on(order)
    if not delivered:
        return EligibilityResult(
            reason_code="not_delivered",
            reason="An exchange can be raised only after the order has been delivered.",
            policy_sections=["4.2"],
            next_action="Explain that the window starts at delivery.",
        )

    if _request_day(request_date) > delivered + timedelta(days=RETURN_WINDOW_DAYS):
        return EligibilityResult(
            reason_code="outside_window",
            reason=f"The {RETURN_WINDOW_DAYS}-calendar-day exchange window has expired.",
            policy_sections=["4.2"],
            next_action="Explain that the window has expired.",
        )

    if item.category in NON_RETURNABLE_CATEGORIES:
        return EligibilityResult(
            reason_code="non_exchangeable_category",
            reason=f"{item.name} is in a category that cannot be returned or exchanged.",
            policy_sections=["2.3"],
            next_action="Explain the category exclusion.",
        )

    if prior_exchanges >= 1:
        return EligibilityResult(
            reason_code="second_exchange",
            reason="This item has already been exchanged once, so a second exchange needs human approval.",
            policy_sections=["4.4"],
            needs_human=True,
            next_action="Escalate for human approval.",
        )

    return EligibilityResult(
        eligible=True,
        reason_code="eligible",
        reason=f"{item.name} is eligible for one size exchange.",
        conditions=["If the requested size is unavailable, the exchange converts to a refund."],
        policy_sections=["4.1", "4.2", "4.3", "4.4"],
        next_action="Confirm the requested size with the customer, then ask them to confirm.",
    )


def check_delay_credit_eligibility(
    order: Order, request_date: date | datetime, already_issued: bool = False
) -> EligibilityResult:
    """Policy 1.5: a delayed order qualifies for a ₹250 store credit on request."""
    if already_issued:
        return EligibilityResult(
            reason_code="already_issued",
            reason="A delay store credit has already been issued for this order.",
            policy_sections=["1.5"],
            next_action="Tell the customer it is already applied.",
        )

    if order.status == "lost_in_transit":
        return EligibilityResult(
            reason_code="lost_parcel",
            reason="This is a lost-parcel claim, handled by a human agent, not a delay credit.",
            policy_sections=["1.6"],
            needs_human=True,
            next_action="Escalate as a lost-parcel claim.",
        )

    if order.status == "cancelled":
        return EligibilityResult(
            reason_code="cancelled",
            reason="Cancelled orders do not qualify for a delay credit.",
            policy_sections=["1.5"],
            next_action="Explain the cancellation refund path.",
        )

    if order.delivered_at:
        return EligibilityResult(
            reason_code="delivered",
            reason="This order has been delivered, so the delay credit does not apply.",
            policy_sections=["1.5"],
            next_action="Ask whether they need help with something else on the order.",
        )

    expected = order.expected_delivery
    late = order.status == "delayed" or (
        expected
        and _request_day(request_date)
        > to_ist_date(expected) + timedelta(days=DELAY_THRESHOLD_DAYS)
    )
    if not late:
        return EligibilityResult(
            reason_code="not_delayed",
            reason=f"This order is not more than {DELAY_THRESHOLD_DAYS} business days past its expected delivery date.",
            policy_sections=["1.5"],
            next_action="Give the expected delivery date instead.",
        )

    return EligibilityResult(
        eligible=True,
        reason_code="eligible",
        reason=f"Delayed beyond {DELAY_THRESHOLD_DAYS} business days, so a ₹{DELAY_CREDIT_AMOUNT} store credit applies on request.",
        conditions=["No cancellation is required to receive it."],
        policy_sections=["1.5"],
        next_action="Apply the credit and give the customer its reference.",
    )
