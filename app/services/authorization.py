"""Server-side gates on state-changing actions.

The prompt is the weakest layer. These checks sit below the model and cannot be
argued past: a mutation needs a passing eligibility check on the exact order and
item, plus an explicit customer confirmation made on an earlier turn. A model
that hallucinates `initiate_return` gets a refusal, not a return.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models.conversation import PendingAction

# Anchored so "no problem, go ahead" is read as a decline rather than consent —
# erring toward not mutating is the safe direction for this class of mistake.
AFFIRMATIVE_RE = re.compile(
    r"^\s*(?:yes|yeah|yep|yup|confirm|confirmed|please do|go ahead|do it|sounds good|ok(?:ay)?)\b",
    re.I,
)
DECLINE_RE = re.compile(
    r"^\s*(?:no\b|nope|nah|don'?t\b|do not\b|cancel\b|never ?mind|forget it|stop\b|actually,? no)",
    re.I,
)
PROPOSAL_TTL_TURNS = 1

# Denial codes. `VERIFICATION_REQUIRED` says "I need identifiers"; everything
# else that could disclose whether an order exists says `ORDER_NOT_ACCESSIBLE`.
VERIFICATION_REQUIRED = "VERIFICATION_REQUIRED"
ORDER_NOT_ACCESSIBLE = "ORDER_NOT_ACCESSIBLE"
CUSTOMER_NOT_RECOGNISED = "CUSTOMER_NOT_RECOGNISED"
IDENTITY_LOCKED = "IDENTITY_LOCKED"
IDENTIFIERS_NOT_SUPPLIED = "IDENTIFIERS_NOT_SUPPLIED"


def verify_customer_order_access(customer_id: str | None, order_id: str | None) -> dict:
    """Decide, in application code, whether this customer may access this order.

    Deterministic and independently testable: it reads the dataset and compares
    `order.customer_id` to the supplied `customer_id`. Nothing here consults the
    model, and no order object is returned — callers that are authorised fetch
    the order themselves through the customer-scoped repository.

    An unrecognised customer and an order they do not own are reported
    differently, because "that customer id isn't right" is useful and discloses
    nothing. But a valid customer asking about someone else's order and a valid
    customer asking about an order that does not exist both return
    ORDER_NOT_ACCESSIBLE — telling those apart would confirm the order exists.
    """
    from .order_repository import get_order_repository

    missing = [
        name
        for name, value in (("customer_id", customer_id), ("order_id", order_id))
        if not (value or "").strip()
    ]
    if missing:
        return {
            "authorized": False,
            "reason_code": VERIFICATION_REQUIRED,
            "missing": missing,
        }

    repo = get_order_repository()
    customer = customer_id.strip()
    order = order_id.strip().upper()

    if not repo.customer_exists(customer):
        return {
            "authorized": False,
            "reason_code": CUSTOMER_NOT_RECOGNISED,
            "missing": ["customer_id"],
        }

    if repo.get_for_customer(order, customer) is None:
        return {"authorized": False, "reason_code": ORDER_NOT_ACCESSIBLE}

    return {"authorized": True, "customer_id": customer, "order_id": order}


def reads_as_confirmation(message: str) -> bool:
    return bool(AFFIRMATIVE_RE.search(message)) and not reads_as_decline(message)


def reads_as_decline(message: str) -> bool:
    return bool(DECLINE_RE.search(message))


def age_proposal(pending: PendingAction | None) -> PendingAction | None:
    """Carry an un-actioned proposal forward, but not indefinitely.

    A proposal made this turn is confirmable next turn. If the customer neither
    confirms nor declines, it survives one further turn and is then dropped —
    otherwise a stale offer waits for any later "yes" to land on it.
    """
    if pending is None:
        return None
    if pending.fresh:
        return pending.model_copy(update={"fresh": False, "age": 0})
    if pending.age + 1 > PROPOSAL_TTL_TURNS:
        return None
    return pending.model_copy(update={"age": pending.age + 1})


@dataclass
class TurnAuthorization:
    """Authorization facts for one turn. Rebuilt per request, never persisted."""

    customer_id: str
    user_confirmed: bool = False
    # The offer the customer is answering, fixed at the start of the turn. Kept
    # separate from anything proposed during the turn: a check run mid-turn must
    # never be able to redirect an existing confirmation onto a different item.
    incoming: PendingAction | None = None
    proposed: PendingAction | None = None
    looked_up: set[str] = field(default_factory=set)
    found_orders: set[str] = field(default_factory=set)
    eligible_returns: set[tuple[str, str]] = field(default_factory=set)
    eligible_exchanges: set[tuple[str, str]] = field(default_factory=set)

    @staticmethod
    def _key(order_id: str, item_id: str) -> tuple[str, str]:
        return order_id.upper(), item_id.upper()

    # --- preconditions ---------------------------------------------------

    def require_lookup(self, order_id: str) -> str | None:
        if order_id.upper() not in self.looked_up:
            return "Safety check failed: call get_order for this order first."
        return None

    def record_lookup(self, order_id: str, found: bool) -> None:
        self.looked_up.add(order_id.upper())
        if found:
            self.found_orders.add(order_id.upper())

    def grant_return(self, order_id: str, item_id: str) -> None:
        self.eligible_returns.add(self._key(order_id, item_id))

    def grant_exchange(self, order_id: str, item_id: str) -> None:
        self.eligible_exchanges.add(self._key(order_id, item_id))

    def has_return_grant(self, order_id: str, item_id: str) -> bool:
        return self._key(order_id, item_id) in self.eligible_returns

    def has_exchange_grant(self, order_id: str, item_id: str) -> bool:
        return self._key(order_id, item_id) in self.eligible_exchanges

    def confirmed_for(self, kind: str, order_id: str, item_id: str) -> bool:
        """Both halves are required: a proposal made on an earlier turn AND an
        affirmative now, matching this exact customer, kind, order, and item.

        One ambiguous message can never be enough, and a confirmation cannot be
        transferred — not to another item, another action, or another customer.
        """
        return bool(
            self.user_confirmed
            and self.incoming
            and self.incoming.matches(self.customer_id, kind, order_id, item_id)
        )

    def propose(self, action: PendingAction) -> None:
        self.proposed = action

    @property
    def pending(self) -> PendingAction | None:
        """What should carry to the next turn: this turn's offer, else the
        one still outstanding."""
        return self.proposed or self.incoming

    def clear_pending(self) -> None:
        self.incoming = None
        self.proposed = None
