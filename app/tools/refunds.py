"""Refund timing and shipping charges, resolved against a verified order.

Both answers are joins between order data and a policy clause, which is exactly
the kind of thing the model should not be doing from a retrieved passage: read
the table wrong once and a UPI customer is told to wait ten days.

`get_refund_timing` raises the handoff itself when the order is
cash-on-delivery. Policy 3.3 requires a human to collect bank details over a
secure link, and leaving that to the model to notice is how an assistant ends up
asking for an account number in chat.
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ..models.tool_results import ORDER_NOT_ACCESSIBLE, RefundTimingResult, ShippingQuoteResult
from ..services.authorization import VERIFICATION_REQUIRED
from ..services.eligibility import SHIPPING_MODES, get_refund_timing, quote_shipping_fee
from .context import ToolContext


class RefundTimingArgs(BaseModel):
    order_id: str = Field(description="Trendly order id, in the format TR-0000.")


class ShippingQuoteArgs(BaseModel):
    order_id: str = Field(description="Trendly order id, in the format TR-0000.")
    shipping_mode: str | None = Field(
        default=None,
        description="One of standard or express. Defaults to standard when the customer has not said.",
    )


def _unverified_refund() -> dict:
    return RefundTimingResult(
        ok=False,
        known=False,
        reason_code=VERIFICATION_REQUIRED,
        next_action="Ask for the customer ID and order ID, then call verify_identity.",
    ).model_dump(exclude_none=True)


def build_get_refund_timing(ctx: ToolContext) -> StructuredTool:
    def get_refund_timing_tool(order_id: str) -> dict:
        ctx.record("get_refund_timing")

        if not ctx.is_verified:
            return _unverified_refund()

        order = ctx.repository.get_for_customer(order_id, ctx.customer_id)
        if not order:
            return RefundTimingResult(
                ok=False,
                known=False,
                reason_code=ORDER_NOT_ACCESSIBLE,
                next_action="Say only that you can help with orders on their own account.",
            ).model_dump(exclude_none=True)

        timing = get_refund_timing(order)
        ctx.cite(timing["policy_sections"])

        case_reference = None
        if timing["requires_human"]:
            # Deterministic, not advisory: a COD refund is handed off here rather
            # than relying on the model to remember policy 3.3.
            escalate = next(t for t in ctx.pending_toolset if t.name == "escalate_to_human")
            outcome = escalate.invoke(
                {
                    "reason": "cod_refund_requires_secure_handling",
                    "summary": (
                        f"Cash-on-delivery refund for {order.order_id}. Refund route is "
                        f"{timing['refund_destination']}, {timing['refund_window']} after inspection "
                        f"({timing['inspection_window']})."
                    ),
                    "order_id": order.order_id,
                    "required_human_action": (
                        "Collect the customer's bank details using Trendly's secure link, then "
                        "process the refund. Bank details must never be taken in chat."
                    ),
                }
            )
            case_reference = outcome.get("case_reference")

        return RefundTimingResult(
            known=timing["known"],
            payment_method=timing["payment_method"],
            refund_destination=timing.get("refund_destination"),
            refund_window=timing.get("refund_window"),
            inspection_window=timing.get("inspection_window"),
            requires_human=timing["requires_human"],
            reason_code=timing["reason_code"],
            policy_sections=timing["policy_sections"],
            next_action=timing["next_action"],
            case_reference=case_reference,
            guidance=(
                "Tell the customer the timing, say a colleague will contact them over a secure "
                "link for their bank details, and give them the case reference. Never ask for a "
                "bank account, IFSC, card number, or CVV."
                if timing["requires_human"]
                else "State this timing directly and cite section 3.1. Do not quote the whole table."
            ),
        ).model_dump(exclude_none=True)

    return StructuredTool.from_function(
        func=get_refund_timing_tool,
        name="get_refund_timing",
        description=(
            "Work out when THIS customer's refund arrives and where it goes, from the order's own "
            "payment method and policy 3.1. Use it for any 'when do I get my money back' question "
            "instead of quoting the policy table — the table covers every payment method, and the "
            "customer only has one. Cash-on-delivery refunds are handed to a human automatically."
        ),
        args_schema=RefundTimingArgs,
    )


def build_quote_shipping_fee(ctx: ToolContext) -> StructuredTool:
    def quote_shipping_fee_tool(order_id: str, shipping_mode: str | None = None) -> dict:
        ctx.record("quote_shipping_fee")

        if not ctx.is_verified:
            return ShippingQuoteResult(
                ok=False,
                available=False,
                reason_code=VERIFICATION_REQUIRED,
                reason="This conversation has not been verified yet.",
            ).model_dump(exclude_none=True)

        order = ctx.repository.get_for_customer(order_id, ctx.customer_id)
        if not order:
            return ShippingQuoteResult(
                ok=False,
                available=False,
                reason_code=ORDER_NOT_ACCESSIBLE,
                reason="No matching order is available for this signed-in customer.",
            ).model_dump(exclude_none=True)

        quote = quote_shipping_fee(order.total, shipping_mode or "standard", order.payment_method)
        ctx.cite(quote["policy_sections"])
        return ShippingQuoteResult(
            ok=quote["available"],
            available=quote["available"],
            shipping_mode=quote.get("shipping_mode"),
            fee=quote.get("fee"),
            currency=quote.get("currency"),
            reason_code=quote["reason_code"],
            reason=quote["reason"],
            policy_sections=quote["policy_sections"],
        ).model_dump(exclude_none=True)

    return StructuredTool.from_function(
        func=quote_shipping_fee_tool,
        name="quote_shipping_fee",
        description=(
            "Work out the shipping charge on THIS order from its total and payment method, under "
            f"policy 1.3. Modes are {' and '.join(SHIPPING_MODES)}. Use it rather than reciting the "
            "thresholds, and note that express is unavailable on cash-on-delivery orders."
        ),
        args_schema=ShippingQuoteArgs,
    )
