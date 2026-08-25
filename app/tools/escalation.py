"""Human escalation and the one compensation the agent may grant."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ..models.tool_results import (
    ActionResult,
    EligibilityResult,
    EscalationResult,
    EscalationSummary,
)
from ..services.authorization import VERIFICATION_REQUIRED
from ..services.eligibility import DELAY_CREDIT_AMOUNT, check_delay_credit_eligibility
from .context import ToolContext


# Keyword → bucket. Deliberately matched rather than enumerated: rejecting an
# unlisted reason would raise mid-turn and fail the customer's request, which is
# a worse outcome than a coarse label.
_REASON_KEYWORDS = (
    ("lost", "lost_parcel"),
    ("damage", "damaged_or_wrong_item"),
    ("defect", "damaged_or_wrong_item"),
    ("wrong", "damaged_or_wrong_item"),
    ("cod", "cod_refund_bank_details"),
    ("bank", "cod_refund_bank_details"),
    ("second_exchange", "second_exchange_request"),
    ("exchange_limit", "second_exchange_request"),
    ("discount", "compensation_request"),
    ("coupon", "compensation_request"),
    ("compensat", "compensation_request"),
    ("credit", "compensation_request"),
    ("refund_dispute", "compensation_request"),
    ("policy", "policy_not_covered"),
    ("not_covered", "policy_not_covered"),
    ("loop", "tool_loop_limit"),
    ("fail", "action_failed"),
    ("error", "action_failed"),
)


def normalise_reason(raw: str) -> str:
    """Map a free-text reason onto the closed set above.

    The model coins categories when left to itself — a live run produced
    `off_topic`, which is not an escalation reason at all — and a queue keyed on
    invented codes cannot be triaged or reported on. Anything unrecognised
    becomes `other` rather than an error.
    """
    text = (raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    for needle, bucket in _REASON_KEYWORDS:
        if needle in text:
            return bucket
    return "other"


class EscalateArgs(BaseModel):
    reason: str = Field(
        description=(
            "Why a human is needed, e.g. lost_parcel, policy_not_covered, damaged_or_wrong_item, "
            "cod_refund_bank_details, compensation_request. There is no reason for an off-topic "
            "question: decline those instead of escalating."
        )
    )
    summary: str = Field(
        description=(
            "A factual handoff a human can act on without re-reading the chat: who the customer is, "
            "which order, what they asked for, what the tools returned, and what is still needed."
        )
    )
    order_id: str | None = Field(default=None, description="Related order id, if there is one.")
    required_human_action: str | None = Field(
        default=None,
        description=(
            "The single concrete thing the human agent must do next, e.g. 'Offer a free "
            "replacement or full refund' or 'Collect bank details over a secure link'."
        ),
    )


class DelayCreditArgs(BaseModel):
    order_id: str = Field(description="Trendly order id for the delayed order.")


def build_escalate_to_human(ctx: ToolContext) -> StructuredTool:
    def escalate_to_human(
        reason: str,
        summary: str,
        order_id: str | None = None,
        required_human_action: str | None = None,
    ) -> dict:
        ctx.record("escalate_to_human")

        # Off-topic escalation is handled in the prompt and by the dead-end
        # guard declining to *force* one, not by refusing here. A tool-level
        # block was tried and reverted: "not a Trendly matter" is not reliably
        # detectable from keywords, and the version that stopped "who won the
        # cricket match?" also stopped "this fabric gave me a rash" — a real
        # product-safety escalation. Wrongly refusing that is far worse than
        # occasionally filing a junk case, so the model keeps the final say.
        bucket = normalise_reason(reason)
        text = summary.strip()
        if "lost" in reason.lower() or "lost" in text.lower():
            text = f"Lost-parcel claim: {text}"
            ctx.cite(["1.6"])
        if "second_exchange" in reason.lower():
            ctx.cite(["4.4"])
        if order_id:
            text = f"[{order_id.upper()}] {text}"

        # The record is assembled from what this turn actually did, so the human
        # sees the tools that ran rather than the model's account of them.
        structured = EscalationSummary(
            reason=bucket,
            customer_id=ctx.customer_id,
            order_id=(order_id or ctx.verified_order_id or "").upper() or None,
            customer_request=text,
            policy_sections=list(ctx.policy_sections),
            facts_checked=[t for t in ctx.trace if t != "escalate_to_human"],
            eligibility_result=ctx.last_eligibility,
            action_attempted=ctx.last_action_attempted,
            action_result=ctx.last_action_result,
            required_human_action=(required_human_action or "").strip()
            or "Review the case and decide the outcome with the customer.",
        )

        reference = ctx.ledger.new_case_reference()
        ctx.add_action(
            "escalated",
            reference,
            {
                "reason": bucket,
                "summary": structured.render(),
                "order_id": structured.order_id,
                "structured": structured.model_dump(exclude_none=True),
            },
        )
        return EscalationResult(
            escalated=True,
            case_reference=reference,
            summary=structured.render(),
            structured=structured,
            # Read immediately before the reply is written, so the ordering
            # instruction lands here rather than in the system prompt: the
            # previous wording ("give the reference and say what happens next")
            # reliably produced replies that opened with "I've handed this over".
            guidance=(
                "Write the reply in this order: first acknowledge what the customer is dealing "
                "with in your own words, then give them this case reference, then say what happens "
                "next. Do not open with 'I've handed this over' — that leads with your process "
                "instead of their problem. Apologise once and plainly: never write 'sorry again' "
                "or 'again, I'm sorry', which implies an earlier apology the customer did not get."
            ),
        ).model_dump(exclude_none=True)

    return StructuredTool.from_function(
        func=escalate_to_human,
        name="escalate_to_human",
        description=(
            "Hand a Trendly matter to a human and return a case reference: lost parcels, questions "
            "the policy does not cover, anything outside your authority. Not for messages that are "
            "not Trendly support at all (programming, general knowledge, chit-chat) — decline those "
            "instead. Always set required_human_action; the colleague has not read the conversation."
        ),
        args_schema=EscalateArgs,
    )


def build_issue_delay_credit(ctx: ToolContext) -> StructuredTool:
    def issue_delay_credit(order_id: str) -> dict:
        ctx.record("issue_delay_credit")

        if not ctx.is_verified:
            return ActionResult(
                ok=False,
                created=False,
                message="This conversation has not been verified yet.",
                detail={"reason_code": VERIFICATION_REQUIRED},
                guidance="Ask for the customer ID and order ID, then call verify_identity.",
            ).model_dump(exclude_none=True)

        blocked = ctx.auth.require_lookup(order_id)
        if blocked:
            return ActionResult(ok=False, created=False, message=blocked).model_dump(exclude_none=True)

        order = ctx.repository.get_for_customer(order_id, ctx.customer_id)
        if not order:
            return ActionResult(
                ok=False, created=False, message="Order not found for this customer."
            ).model_dump(exclude_none=True)

        verdict: EligibilityResult = check_delay_credit_eligibility(
            order, ctx.as_of, already_issued=ctx.ledger.credit_issued(order_id)
        )
        ctx.cite(verdict.policy_sections)
        if not verdict.eligible:
            return ActionResult(
                ok=False,
                created=False,
                message=verdict.reason,
                detail={"reason_code": verdict.reason_code},
                guidance=verdict.next_action,
            ).model_dump(exclude_none=True)

        # No separate confirmation turn: policy 1.5 grants this "on request", the
        # amount is fixed, it is capped at one per order, and it is a credit to
        # the customer rather than a change to their order.
        detail = {"order_id": order_id.upper(), "amount": DELAY_CREDIT_AMOUNT, "currency": "INR"}
        record, created = ctx.ledger.submit_credit(ctx.customer_id, order_id, detail)
        ctx.add_action("credit_issued", record.reference, record.detail)
        return ActionResult(
            created=True,
            reference=record.reference,
            detail=record.detail if created else {**record.detail, "replayed": True},
            message=f"₹{DELAY_CREDIT_AMOUNT} store credit applied. No cancellation required.",
        ).model_dump(exclude_none=True)

    return StructuredTool.from_function(
        func=issue_delay_credit,
        name="issue_delay_credit",
        description=(
            f"Issue the policy 1.5 store credit (₹{DELAY_CREDIT_AMOUNT}) for an order delayed more "
            "than 3 business days. This is the ONLY compensation you can grant; the amount is fixed "
            "and it is once per order. You must call get_order first. Use it when the customer asks "
            "about compensation for a delay — never promise a credit without calling this. Any other "
            "discount, coupon, waiver, or goodwill request must be refused and escalated."
        ),
        args_schema=DelayCreditArgs,
    )
