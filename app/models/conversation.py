"""Typed multi-turn state.

Only three things persist across turns: recent messages, the active order, and at
most one outstanding proposal. Every fact is re-fetched per turn rather than
remembered, so the agent cannot drift from the order record as a conversation
lengthens.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, TypedDict

from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


class VerificationState(str, Enum):
    """Where a conversation is in the identity lifecycle.

    The enum is a description, never a grant. `ConversationState.is_verified`
    requires the state *and* the three facts it claims, so setting the state
    alone — or merely mentioning "C-100" and "TR-4524" — opens nothing. Only
    `mark_verified`, called on the success path of the deterministic ownership
    check, can produce a consistent VERIFIED.
    """

    UNVERIFIED = "unverified"
    IDENTIFIERS_COLLECTED = "identifiers_collected"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    VERIFICATION_FAILED = "verification_failed"


class PendingAction(BaseModel):
    """An offer made to the customer, awaiting their explicit agreement.

    Deliberately short-lived: created when an eligibility check passes, retired
    on an explicit decline, and expired after one quiet turn. A stale offer that
    lingers is one that a later unrelated "yes" can land on.
    """

    customer_id: str
    kind: Literal["return", "exchange"]
    order_id: str
    item_id: str
    reason: str | None = None
    requested_size: str | None = None
    fresh: bool = True
    age: int = 0

    def matches(self, customer_id: str, kind: str, order_id: str, item_id: str) -> bool:
        """An offer authorises exactly one action, for one customer, once.

        All four must still match: a proposal made for one customer, kind,
        order, and item can never authorise any other combination.
        """
        return (
            self.customer_id == customer_id
            and self.kind == kind
            and self.order_id.upper() == order_id.upper()
            and self.item_id.upper() == item_id.upper()
        )


class ToolInvocation(BaseModel):
    name: str
    ok: bool = True
    detail: str = ""


class ConversationState(BaseModel):
    """Per-session state held outside the graph, keyed by `session_id`."""

    session_id: str
    # The identity asserted by the channel, if any. `None` means the session has
    # not been identified yet and the agent must collect identifiers.
    customer_id: str | None = None
    # Set only once the application has verified an identity — either from a
    # trusted channel field or through `verify_identity` during the conversation.
    # This is what every order tool scopes to; the model can never write it.
    verified_customer_id: str | None = None
    # The last order whose ownership was confirmed for the verified customer.
    active_order_id: str | None = None
    order_verified: bool = False
    verification_state: VerificationState = VerificationState.UNVERIFIED
    messages: list[dict] = Field(default_factory=list)
    pending_action: PendingAction | None = None
    escalation_status: str | None = None
    # Set when the assistant asks which item on a multi-item order. The reply
    # ("the tee") carries no intent of its own, so the intent has to be carried
    # for it or the next turn reads as a plain status query.
    awaiting_item_for: Literal["return", "exchange"] | None = None

    @property
    def has_verified_customer(self) -> bool:
        """An identity has been established — enough to reach customer-scoped tools.

        Distinct from `is_verified`, which additionally requires a confirmed
        order. Tools need only this, because every one of them ownership-checks
        the specific order it is given.
        """
        return (
            self.verification_state is VerificationState.VERIFIED
            and bool(self.verified_customer_id)
        )

    @property
    def is_verified(self) -> bool:
        """Fully verified: an identity *and* an order confirmed to belong to it.

        This is the authoritative definition; `ToolContext.is_verified` mirrors
        `has_verified_customer` from it and never decides for itself.
        """
        return (
            self.verification_state is VerificationState.VERIFIED
            and bool(self.verified_customer_id)
            and bool(self.active_order_id)
            and self.order_verified
        )

    def mark_identifiers_collected(self) -> None:
        """Identifiers seen but not yet authorised. Grants nothing."""
        if not self.is_verified:
            self.verification_state = VerificationState.IDENTIFIERS_COLLECTED

    def mark_verifying(self) -> None:
        if not self.is_verified:
            self.verification_state = VerificationState.VERIFYING

    def mark_verification_failed(self) -> None:
        """A failed attempt never clears an existing verification, and never
        creates one — the customer may simply retry."""
        if not self.is_verified:
            self.verification_state = VerificationState.VERIFICATION_FAILED

    def mark_verified(self, customer_id: str, order_id: str) -> None:
        """The only transition into VERIFIED, and it sets every backing fact."""
        self.customer_id = customer_id
        self.verified_customer_id = customer_id
        self.active_order_id = order_id
        self.order_verified = True
        self.verification_state = VerificationState.VERIFIED


class AgentState(TypedDict, total=False):
    """LangGraph working state for a single turn."""

    messages: Annotated[list, add_messages]
    session_id: str
    customer_id: str
    active_order_id: str | None
    pending_action: dict | None
    tool_history: list[dict]
    escalation_status: str | None
    nudges: int
    force_tools: bool
