"""Structured results every tool returns.

Tools return typed data rather than prose so the model cannot mistake a refusal
for a success, and so guardrails can inspect what actually happened. Every
failure carries a `guidance` field: a refusal that explains the protocol lets the
model recover inside the same reasoning loop instead of apologising to the
customer or inventing an outcome.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .order import Order


class ToolResult(BaseModel):
    ok: bool = True
    guidance: str | None = Field(
        default=None,
        description="What the model should do next when this result is not what it wanted.",
    )


# Single denial code for every "you may not see this order" outcome. It is
# deliberately the same whether the order does not exist or belongs to someone
# else — distinguishing them would itself disclose that it exists.
ORDER_NOT_ACCESSIBLE = "ORDER_NOT_ACCESSIBLE"


class OrderLookupResult(ToolResult):
    authorized: bool = True
    reason_code: str | None = None
    found: bool = False
    # A plain dict rather than an Order, because the model-facing record
    # deliberately omits `customer_id`: authorization already used it, and
    # putting an identity value in the model's context serves nothing.
    order: dict | None = None
    message: str | None = None


class VerificationResult(ToolResult):
    """Outcome of the application's customer+order ownership check."""

    authorized: bool = False
    reason_code: str | None = None
    customer_id: str | None = None
    order_id: str | None = None
    message: str | None = None


class OrderListResult(ToolResult):
    """Only ever the trusted customer's own orders."""

    orders: list[dict] = Field(default_factory=list)
    count: int = 0


class PolicyPassage(BaseModel):
    section_number: str
    section_title: str
    text: str
    score: float = 0.0
    source: str = "trendly_policy.md"


class PolicySearchResult(ToolResult):
    query: str
    passages: list[PolicyPassage] = Field(default_factory=list)
    retrieval: str = Field(default="hybrid", description="semantic | lexical | hybrid | fallback")
    # Topic words from the question that appear nowhere in the policy. A high
    # similarity score says the passage is the closest text, not that it
    # answers anything; these are the words that show it does not.
    unsupported_terms: list[str] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.passages


class RefundTimingResult(ToolResult):
    """Policy 3.1 resolved against one order's payment method."""

    known: bool = False
    payment_method: str | None = None
    refund_destination: str | None = None
    refund_window: str | None = None
    inspection_window: str | None = None
    requires_human: bool = False
    reason_code: str = ""
    policy_sections: list[str] = Field(default_factory=list)
    next_action: str = ""
    case_reference: str | None = None


class ShippingQuoteResult(ToolResult):
    """Policy 1.3 resolved against one order's total and payment method."""

    available: bool = False
    shipping_mode: str | None = None
    fee: float | None = None
    currency: str | None = None
    reason_code: str = ""
    reason: str = ""
    policy_sections: list[str] = Field(default_factory=list)


class EligibilityResult(ToolResult):
    eligible: bool = False
    reason_code: str = ""
    reason: str = ""
    deduction: float = 0
    conditions: list[str] = Field(default_factory=list)
    policy_sections: list[str] = Field(default_factory=list)
    next_action: str = ""
    needs_human: bool = False


class ActionResult(ToolResult):
    created: bool = False
    reference: str | None = None
    detail: dict = Field(default_factory=dict)
    requires_confirmation: bool = False
    message: str = ""


class EscalationSummary(BaseModel):
    """What a human agent needs in order to pick this up cold.

    Assembled by the application from what the turn actually did, not narrated by
    the model: the tools it ran, the verdicts they returned, and the policy
    sections cited. The model contributes the customer's problem in their own
    terms and what it believes the human must do.

    Sensitive fields are never carried here — banking content is refused before
    the model sees it, and nothing in this record reaches for customer contact
    details that the handoff does not need.
    """

    reason: str
    customer_id: str | None = None
    order_id: str | None = None
    item_id: str | None = None
    customer_request: str = ""
    policy_sections: list[str] = Field(default_factory=list)
    facts_checked: list[str] = Field(default_factory=list)
    eligibility_result: str | None = None
    action_attempted: str | None = None
    action_result: str | None = None
    required_human_action: str = ""

    def render(self) -> str:
        """One-paragraph form for the API's `handoff_summary` field."""
        parts = [f"Reason: {self.reason}."]
        if self.customer_id:
            parts.append(f"Customer {self.customer_id}.")
        if self.order_id:
            parts.append(f"Order {self.order_id}{f' item {self.item_id}' if self.item_id else ''}.")
        if self.customer_request:
            parts.append(f"Request: {self.customer_request}")
        if self.policy_sections:
            parts.append(f"Policy: {', '.join(self.policy_sections)}.")
        if self.facts_checked:
            parts.append(f"Checked: {'; '.join(self.facts_checked)}.")
        if self.eligibility_result:
            parts.append(f"Eligibility: {self.eligibility_result}.")
        if self.action_attempted:
            parts.append(f"Attempted: {self.action_attempted} -> {self.action_result or 'no result'}.")
        if self.required_human_action:
            parts.append(f"Needed from you: {self.required_human_action}")
        return " ".join(parts)


class EscalationResult(ToolResult):
    escalated: bool = False
    case_reference: str | None = None
    summary: str = ""
    structured: EscalationSummary | None = None
