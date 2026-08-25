"""Order lookup tool."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ..models.tool_results import (
    ORDER_NOT_ACCESSIBLE,
    OrderListResult,
    OrderLookupResult,
    VerificationResult,
)
from ..services.authorization import (
    CUSTOMER_NOT_RECOGNISED,
    IDENTIFIERS_NOT_SUPPLIED,
    IDENTITY_LOCKED,
    VERIFICATION_REQUIRED,
    verify_customer_order_access,
)
from .context import ToolContext


class GetOrderArgs(BaseModel):
    order_id: str = Field(description="Trendly order id, in the format TR-0000.")


class ListMyOrdersArgs(BaseModel):
    """No parameters: whose orders these are is not the model's to decide."""


class VerifyIdentityArgs(BaseModel):
    customer_id: str = Field(
        description=(
            "The customer id exactly as the customer typed it. Do not invent or complete one; "
            "if they have not given it, ask."
        )
    )
    order_id: str = Field(
        description=(
            "The order id exactly as the customer typed it. Do not invent or complete one; "
            "if they have not given it, ask."
        )
    )


def build_verify_identity(ctx: ToolContext) -> StructuredTool:
    def verify_identity(customer_id: str, order_id: str) -> dict:
        """Submit identifiers the customer gave for the application to check.

        The model extracts the two ids from natural language; it does not decide
        whether they are valid or related. This function hands them to
        deterministic application code and stores the outcome as session state.
        """
        ctx.record("verify_identity")
        ctx.verification_phase = "verifying"

        supplied = (customer_id or "").strip()

        # Identifiers must have come from the customer. A model that helpfully
        # fills in a plausible pair — or repeats one from its own instructions —
        # would otherwise verify a session nobody identified.
        said = ctx.customer_utterances.upper()
        offered = [v for v in (supplied, (order_id or "").strip()) if v]
        if said and any(v.upper() not in said for v in offered):
            return VerificationResult(
                ok=False,
                authorized=False,
                reason_code=IDENTIFIERS_NOT_SUPPLIED,
                message="Those identifiers did not come from the customer.",
                guidance=(
                    "Ask the customer for their customer ID and order ID and submit exactly what "
                    "they type. Never guess, complete, or invent either value."
                ),
            ).model_dump(exclude_none=True)
        # Once a session is verified, it stays with that customer. A message
        # claiming a different identity is refused rather than re-verified.
        if ctx.customer_id and supplied and supplied != ctx.customer_id:
            ctx.verification_phase = "failed"
            return VerificationResult(
                ok=False,
                authorized=False,
                reason_code=IDENTITY_LOCKED,
                message="This conversation is already verified for a different customer account.",
                guidance=(
                    "Tell the customer you can only continue with the account this conversation "
                    "was verified for, and that a new conversation is needed to use another. Do "
                    "not reveal which account that is."
                ),
            ).model_dump(exclude_none=True)

        outcome = verify_customer_order_access(supplied or ctx.customer_id, order_id)

        if not outcome["authorized"]:
            code = outcome["reason_code"]
            ctx.verification_phase = (
                "identifiers_collected" if code == VERIFICATION_REQUIRED else "failed"
            )
            if code == VERIFICATION_REQUIRED:
                message = "Both a customer ID and an order ID are needed."
                guidance = f"Ask only for what is missing: {', '.join(outcome.get('missing', []))}."
            elif code == CUSTOMER_NOT_RECOGNISED:
                message = "I couldn't verify that customer ID."
                guidance = "Ask the customer to check the customer ID and give it again."
            else:
                message = "That order is not available on the verified customer account."
                guidance = (
                    "Say only that you can help with orders on their own account. Do not confirm "
                    "whether this order exists, and do not name another customer."
                )
            return VerificationResult(
                ok=False, authorized=False, reason_code=code, message=message, guidance=guidance
            ).model_dump(exclude_none=True)

        # Verified. Both facts become application state for the rest of the session.
        ctx.customer_id = outcome["customer_id"]
        # And for the rest of *this* turn. `verify_customer_order_access` is the
        # deterministic ownership check, so passing it is exactly what being
        # verified means. Without this the customer who opens with "I'm C-101,
        # order TR-4530" is verified and then refused by every order tool until
        # their next message, because `is_verified` requires both facts.
        ctx.verified = True
        ctx.auth.customer_id = outcome["customer_id"]
        ctx.verified_order_id = outcome["order_id"]
        ctx.newly_verified = {
            "customer_id": outcome["customer_id"],
            "order_id": outcome["order_id"],
        }
        ctx.verification_phase = "verified"
        ctx.auth.record_lookup(outcome["order_id"], found=True)
        return VerificationResult(
            authorized=True,
            customer_id=outcome["customer_id"],
            order_id=outcome["order_id"],
            message="Verified. This order is on the customer's account.",
            guidance=(
                "Verification is done. Now answer what the customer actually asked, in this same "
                "turn: call the order and eligibility tools you need and reply with the result. "
                "Do not stop here to ask what they want — they have already told you — and do not "
                "ask for these ids again in this conversation."
            ),
        ).model_dump(exclude_none=True)

    return StructuredTool.from_function(
        func=verify_identity,
        name="verify_identity",
        description=(
            "Verify a customer ID and order ID together before any order-specific help. Call it as "
            "soon as the customer has supplied both, in whatever phrasing they used, passing "
            "exactly the values they typed. Never invent, complete, or guess either id — if you "
            "do not have one, ask for it instead. The application checks that the order belongs "
            "to that customer; you never decide that yourself. Once it succeeds the conversation "
            "stays verified, so do not ask for the ids again."
        ),
        args_schema=VerifyIdentityArgs,
    )


def build_get_order(ctx: ToolContext) -> StructuredTool:
    def get_order(order_id: str) -> dict:
        ctx.record("get_order")

        if not ctx.is_verified:
            return OrderLookupResult(
                ok=False,
                authorized=False,
                reason_code=VERIFICATION_REQUIRED,
                found=False,
                message="This conversation has not been verified yet.",
                guidance=(
                    "Ask the customer for their customer ID and order ID, then call "
                    "verify_identity with both. Do not guess either value."
                ),
            ).model_dump(exclude_none=True)

        order = ctx.repository.get_for_customer(order_id, ctx.customer_id)
        ctx.auth.record_lookup(order_id, found=order is not None)

        if not order:
            # One denial for both "no such order" and "not this customer's
            # order". Telling them apart would itself disclose that the order
            # exists, so no order field of any kind appears in this result.
            return OrderLookupResult(
                ok=False,
                authorized=False,
                reason_code=ORDER_NOT_ACCESSIBLE,
                found=False,
                message="No matching order is available for this signed-in customer.",
                guidance=(
                    "Tell the customer only that you can help with orders on their own account. Do "
                    "not speculate about whether this order exists elsewhere, do not name another "
                    "customer, and do not ask them for a different customer id."
                ),
            ).model_dump(exclude_none=True)

        return OrderLookupResult(
            authorized=True,
            found=True,
            order=order.model_dump(exclude={"customer_id"}, exclude_none=True),
        ).model_dump(exclude_none=True)

    return StructuredTool.from_function(
        func=get_order,
        name="get_order",
        description=(
            "Look up one of the signed-in customer's orders and return its full record: status, "
            "carrier, tracking, dates, payment method, and line items. Call this before discussing "
            "or acting on any order. Identity is taken from the authenticated session, so you "
            "cannot look up anyone else's order."
        ),
        args_schema=GetOrderArgs,
    )


def build_list_my_orders(ctx: ToolContext) -> StructuredTool:
    def list_my_orders() -> dict:
        ctx.record("list_my_orders")

        if not ctx.is_verified:
            return OrderListResult(
                ok=False,
                guidance=(
                    "This conversation has not been verified. Ask for the customer ID and an order "
                    "ID, then call verify_identity before listing anything."
                ),
            ).model_dump(exclude_none=True)

        orders = ctx.repository.orders_for_customer(ctx.customer_id)
        for order in orders:
            ctx.auth.record_lookup(order.order_id, found=True)
        # A summary, not the full records: enough to let the customer pick one,
        # without putting every field of every order in the model's context.
        summary = [
            {
                "order_id": o.order_id,
                "status": o.status,
                "placed_at": o.placed_at,
                "items": [i.name for i in o.items],
            }
            for o in orders
        ]
        return OrderListResult(orders=summary, count=len(summary)).model_dump(exclude_none=True)

    return StructuredTool.from_function(
        func=list_my_orders,
        name="list_my_orders",
        description=(
            "List the signed-in customer's own orders — id, status, placed date, and item names. "
            "Use it when they ask what orders they have, or refer to an order without giving an id. "
            "It takes no parameters and can only ever return this customer's orders."
        ),
        args_schema=ListMyOrdersArgs,
    )
