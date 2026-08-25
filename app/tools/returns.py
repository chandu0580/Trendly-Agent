"""Return eligibility and return creation."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ..models.conversation import PendingAction
from ..models.tool_results import ORDER_NOT_ACCESSIBLE, ActionResult, EligibilityResult
from ..services.authorization import VERIFICATION_REQUIRED
from ..services.eligibility import RETURN_REASONS, check_return_eligibility
from .context import ToolContext


class CheckReturnArgs(BaseModel):
    order_id: str = Field(description="Trendly order id, in the format TR-0000.")
    item_id: str = Field(description="The item's SKU, in the format TR-XXX-000.")
    reason: str | None = Field(
        default=None,
        description=(
            "One of change_of_mind, damaged, defective, wrong_item. Defaults to change_of_mind. "
            "Pass damaged, defective, or wrong_item only when the customer has already said so — "
            "those unlock the policy 6.2 exception."
        ),
    )


class InitiateReturnArgs(BaseModel):
    order_id: str = Field(description="Trendly order id.")
    item_id: str = Field(description="The item's SKU.")
    reason: str | None = Field(default=None, description="The return reason used for the check.")


def build_check_return_eligibility(ctx: ToolContext) -> StructuredTool:
    def check_return(order_id: str, item_id: str, reason: str | None = None) -> dict:
        ctx.record("check_return_eligibility")

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

        resolved = (reason or "change_of_mind").lower()
        if resolved not in RETURN_REASONS:
            resolved = "change_of_mind"

        result = check_return_eligibility(
            order,
            item_id,
            ctx.as_of,
            resolved,
            already_returned=bool(ctx.ledger.return_count(order_id, item_id)),
        )
        ctx.cite(result.policy_sections)
        ctx.last_eligibility = f"{result.reason_code}: {result.reason}"

        if result.eligible:
            ctx.auth.grant_return(order_id, item_id)
            ctx.auth.propose(
                PendingAction(
                    customer_id=ctx.customer_id,
                    kind="return",
                    order_id=order_id.upper(),
                    item_id=item_id.upper(),
                    reason=resolved,
                )
            )
        return result.model_dump(exclude_none=True)

    return StructuredTool.from_function(
        func=check_return,
        name="check_return_eligibility",
        description=(
            "Decide whether an item can be returned, combining the order record with the policy "
            "rules. You must call get_order first. Call this immediately — never ask the customer "
            "why they want to return something before checking, because the window, category, and "
            "final-sale rules do not depend on the reason. This tool's verdict is authoritative: "
            "do not judge eligibility yourself from the order data."
        ),
        args_schema=CheckReturnArgs,
    )


def build_initiate_return(ctx: ToolContext) -> StructuredTool:
    def initiate_return(order_id: str, item_id: str, reason: str | None = None) -> dict:
        ctx.record("initiate_return")

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

        # A replay is not a new mutation: it already passed the grant and
        # confirmation gates the first time. Checked before them so a retry
        # returns the original reference rather than failing a spent proposal.
        existing = ctx.ledger.find(ctx.customer_id, "return", order_id, item_id)
        if existing is not None:
            ctx.add_action("return_created", existing.reference, existing.detail)
            return ActionResult(
                created=True,
                reference=existing.reference,
                detail={**existing.detail, "replayed": True},
                message="A return already exists for this item; this is the same one.",
                guidance=(
                    "Give the customer the existing reference. Do not describe it as a second "
                    "return — nothing new was created."
                ),
            ).model_dump(exclude_none=True)

        if not ctx.auth.has_return_grant(order_id, item_id):
            return ActionResult(
                ok=False,
                created=False,
                message="Safety check failed: check_return_eligibility must return eligible=true for this exact order and item first.",
                guidance="Run check_return_eligibility, then relay its verdict honestly.",
            ).model_dump(exclude_none=True)

        if not ctx.auth.confirmed_for("return", order_id, item_id):
            return ActionResult(
                ok=False,
                created=False,
                requires_confirmation=True,
                message="The customer has not explicitly confirmed this return.",
                guidance=(
                    "Summarise what will happen, ask them to confirm, and call this tool again only "
                    "on the turn they agree. Do not claim anything was created."
                ),
            ).model_dump(exclude_none=True)

        detail = {
            "order_id": order_id.upper(),
            "item_id": item_id.upper(),
            "reason": reason or "change_of_mind",
            "pickup": "pending_schedule",
        }
        record, created = ctx.ledger.submit(
            ctx.customer_id, "return", order_id, item_id, "RET", detail
        )
        ctx.auth.clear_pending()
        ctx.last_action_attempted = "initiate_return"
        ctx.last_action_result = record.reference
        ctx.add_action("return_created", record.reference, record.detail)
        return ActionResult(
            created=True,
            reference=record.reference,
            detail=record.detail if created else {**record.detail, "replayed": True},
            message="Return created." if created else "A return already exists for this item.",
        ).model_dump(exclude_none=True)

    return StructuredTool.from_function(
        func=initiate_return,
        name="initiate_return",
        description=(
            "Create a return. Requires a prior eligible check_return_eligibility AND an explicit "
            "customer confirmation given on a later turn. Calling it before the customer has agreed "
            "is rejected. Never describe a return as created unless this tool returns a reference."
        ),
        args_schema=InitiateReturnArgs,
    )
