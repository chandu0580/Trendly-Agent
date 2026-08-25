"""Exchange eligibility and exchange creation."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ..models.conversation import PendingAction
from ..models.tool_results import ORDER_NOT_ACCESSIBLE, ActionResult, EligibilityResult
from ..services.authorization import VERIFICATION_REQUIRED
from ..services.eligibility import EXCHANGE_KINDS, check_exchange_eligibility
from .context import ToolContext


class CheckExchangeArgs(BaseModel):
    order_id: str = Field(description="Trendly order id.")
    item_id: str = Field(description="The item's SKU.")
    requested_size: str | None = Field(
        default=None, description="The size the customer asked for, if they named one."
    )
    exchange_kind: str | None = Field(
        default=None,
        description=(
            "One of size, colour, style. Defaults to size. Pass colour or style when the customer "
            "asked for those, so the out-of-scope refusal is returned."
        ),
    )


class InitiateExchangeArgs(BaseModel):
    order_id: str = Field(description="Trendly order id.")
    item_id: str = Field(description="The item's SKU.")
    requested_size: str | None = Field(default=None, description="The requested size.")


def build_check_exchange_eligibility(ctx: ToolContext) -> StructuredTool:
    def check_exchange(
        order_id: str,
        item_id: str,
        requested_size: str | None = None,
        exchange_kind: str | None = None,
    ) -> dict:
        ctx.record("check_exchange_eligibility")

        if not ctx.is_verified:
            return EligibilityResult(
                ok=False,
                reason_code=VERIFICATION_REQUIRED,
                reason="This conversation has not been verified yet.",
                next_action="Ask for the customer ID and order ID, then call verify_identity.",
            ).model_dump(exclude_none=True)

        blocked = ctx.auth.require_lookup(order_id)
        if blocked:
            return EligibilityResult(reason_code="lookup_required", reason=blocked).model_dump(
                exclude_none=True
            )

        order = ctx.repository.get_for_customer(order_id, ctx.customer_id)
        if not order:
            # Checked before any eligibility rule is evaluated: an
            # unauthorised caller must not learn the window, category, final-sale
            # state, price, or anything else about someone else's order.
            return EligibilityResult(
                ok=False,
                reason_code=ORDER_NOT_ACCESSIBLE,
                reason="No matching order is available for this signed-in customer.",
                next_action="Tell the customer you can only help with orders on their own account.",
            ).model_dump(exclude_none=True)

        kind = (exchange_kind or "size").lower()
        if kind not in EXCHANGE_KINDS:
            kind = "size"

        result = check_exchange_eligibility(
            order,
            item_id,
            ctx.as_of,
            kind,
            prior_exchanges=ctx.ledger.exchange_count(order_id, item_id),
        )
        ctx.cite(result.policy_sections)
        ctx.last_eligibility = f"{result.reason_code}: {result.reason}"

        if result.eligible:
            ctx.auth.grant_exchange(order_id, item_id)
            ctx.auth.propose(
                PendingAction(
                    customer_id=ctx.customer_id,
                    kind="exchange",
                    order_id=order_id.upper(),
                    item_id=item_id.upper(),
                    requested_size=requested_size,
                )
            )
        return result.model_dump(exclude_none=True)

    return StructuredTool.from_function(
        func=check_exchange,
        name="check_exchange_eligibility",
        description=(
            "Decide whether an item can be exchanged, combining the order record with the policy "
            "rules. You must call get_order first. Call this immediately — never ask which size the "
            "customer wants before checking, because the window, category, and prior-exchange rules "
            "do not depend on it. This tool's verdict is authoritative."
        ),
        args_schema=CheckExchangeArgs,
    )


def build_initiate_exchange(ctx: ToolContext) -> StructuredTool:
    def initiate_exchange(
        order_id: str, item_id: str, requested_size: str | None = None
    ) -> dict:
        ctx.record("initiate_exchange")

        if not ctx.is_verified:
            return ActionResult(
                ok=False,
                created=False,
                message="This conversation has not been verified yet.",
                detail={"reason_code": VERIFICATION_REQUIRED},
                guidance="Ask for the customer ID and order ID, then call verify_identity.",
            ).model_dump(exclude_none=True)

        if not ctx.repository.get_for_customer(order_id, ctx.customer_id):
            return ActionResult(
                ok=False,
                created=False,
                message="No matching order is available for this signed-in customer.",
                detail={"reason_code": ORDER_NOT_ACCESSIBLE},
            ).model_dump(exclude_none=True)

        existing = ctx.ledger.find(ctx.customer_id, "exchange", order_id, item_id)
        if existing is not None:
            ctx.add_action("exchange_created", existing.reference, existing.detail)
            return ActionResult(
                created=True,
                reference=existing.reference,
                detail={**existing.detail, "replayed": True},
                message="An exchange already exists for this item; this is the same one.",
                guidance=(
                    "Give the customer the existing reference. Do not describe it as a second "
                    "exchange — nothing new was created."
                ),
            ).model_dump(exclude_none=True)

        if not ctx.auth.has_exchange_grant(order_id, item_id):
            return ActionResult(
                ok=False,
                created=False,
                message="Safety check failed: check_exchange_eligibility must return eligible=true for this exact order and item first.",
                guidance="Run check_exchange_eligibility, then relay its verdict honestly.",
            ).model_dump(exclude_none=True)

        if not ctx.auth.confirmed_for("exchange", order_id, item_id):
            return ActionResult(
                ok=False,
                created=False,
                requires_confirmation=True,
                message="The customer has not explicitly confirmed this exchange.",
                guidance=(
                    "Summarise what will happen, ask them to confirm, and call this tool again only "
                    "on the turn they agree. Do not claim anything was created."
                ),
            ).model_dump(exclude_none=True)

        detail = {
            "order_id": order_id.upper(),
            "item_id": item_id.upper(),
            "requested_size": requested_size,
            # Policy 4.3: stock is not in the supplied dataset, so availability is
            # reported as unconfirmed rather than invented.
            "availability": "to_be_confirmed",
        }
        record, created = ctx.ledger.submit(
            ctx.customer_id, "exchange", order_id, item_id, "EXC", detail
        )
        ctx.auth.clear_pending()
        ctx.last_action_attempted = "initiate_exchange"
        ctx.last_action_result = record.reference
        ctx.add_action("exchange_created", record.reference, record.detail)
        return ActionResult(
            created=True,
            reference=record.reference,
            detail=record.detail if created else {**record.detail, "replayed": True},
            message="Exchange created; requested-size availability is not yet confirmed.",
            guidance=(
                "Tell the customer availability is still to be confirmed and that an unavailable "
                "size converts to a refund under policy 4.3. Do not state that the size is in stock."
            ),
        ).model_dump(exclude_none=True)

    return StructuredTool.from_function(
        func=initiate_exchange,
        name="initiate_exchange",
        description=(
            "Create a size exchange. Requires a prior eligible check_exchange_eligibility AND an "
            "explicit customer confirmation given on a later turn. Never invent stock availability."
        ),
        args_schema=InitiateExchangeArgs,
    )
