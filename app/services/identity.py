"""Session-level customer identity.

Identity reaches the agent one of two ways:

    client supplies customer_id -> validated here -> bound to the session
    client supplies nothing     -> session starts unverified -> the agent asks,
                                   and `verify_identity` validates what it is told

Either way the result is runtime context. No *data-access* tool takes a
`customer_id` parameter; `verify_identity` accepts one, but it is a claim the
application checks, not an authorisation the model can grant itself.

There is deliberately no default identity. Handing an unidentified caller an
account would be the whole vulnerability, so an unidentified session stays that
way until verification succeeds.

For this prototype `customer_id` is either a client-supplied field or a value the
customer types into the chat. Neither is authentication. In production identity
would come from an authenticated session or token, and this is the only module
that would change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache

from ..config import PROFILES_PATH


class IdentityError(Exception):
    """Rejected before the agent runs. Carries a safe, non-disclosing message."""

    status_code = 400
    error = "identity_error"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class UnknownCustomer(IdentityError):
    status_code = 403
    error = "unknown_customer"


class IdentityConflict(IdentityError):
    status_code = 409
    error = "session_identity_conflict"


@dataclass
class SessionIdentityRegistry:
    """Binds each session to exactly one customer, for the life of the process.

    A session that has spoken as one customer can never speak as another: that
    is what stops a second request from re-pointing an established conversation
    at someone else's orders.
    """

    _bindings: dict[str, str] = field(default_factory=dict)

    def customer_for(self, session_id: str) -> str | None:
        return self._bindings.get(session_id)

    def bind(self, session_id: str, customer_id: str) -> str:
        existing = self._bindings.get(session_id)
        if existing is None:
            self._bindings[session_id] = customer_id
            return customer_id
        if existing != customer_id:
            # Deliberately does not name either customer.
            raise IdentityConflict(
                "This session is already associated with a different signed-in customer. "
                "Start a new session to continue as someone else."
            )
        return existing

    def clear(self) -> None:
        self._bindings.clear()


_registry = SessionIdentityRegistry()


def get_identity_registry() -> SessionIdentityRegistry:
    return _registry


def reset_identity_registry() -> None:
    """Test hook: session bindings must not leak between cases."""
    _registry.clear()


def resolve_trusted_customer(session_id: str, customer_id: str | None) -> str | None:
    """Validate any asserted identity and bind it to the session.

    Returns `None` when the caller supplied nothing and the session has not been
    verified yet — that is a legitimate state, and the agent will ask for the
    identifiers. Raises `UnknownCustomer` for an identity that is not in the
    dataset, and `IdentityConflict` if the session is already bound to someone
    else. Both are raised before the agent is invoked.
    """
    from .order_repository import get_order_repository

    # Precedence: an explicitly asserted identity, else the one this session is
    # already bound to. There is deliberately no default — an unidentified
    # session stays unidentified, and the agent collects the identifiers in
    # conversation rather than being silently handed an account.
    resolved = (
        customer_id.strip()
        if customer_id and customer_id.strip()
        else _registry.customer_for(session_id)
    )
    if resolved is None:
        return None
    if not get_order_repository().customer_exists(resolved):
        raise UnknownCustomer("That customer identity could not be verified.")
    return _registry.bind(session_id, resolved)


def bind_verified_customer(session_id: str, customer_id: str) -> None:
    """Record an identity that the conversation itself verified.

    Called after `verify_identity` succeeds so the session-level binding and the
    conversation-level verification cannot drift apart: a later HTTP request
    naming a different customer still conflicts.
    """
    _registry.bind(session_id, customer_id)


# ----------------------------------------------------------------- profiles


@lru_cache(maxsize=1)
def _profiles() -> dict[str, dict]:
    """Demo contact details, keyed by customer id.

    Deliberately a separate fixture: the supplied `orders.json` carries no name,
    email, or phone, and it must not be modified. Missing or malformed, the
    support desk simply shows no contact panel rather than failing a turn.
    """
    try:
        raw = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
        return raw.get("profiles", {})
    except (OSError, ValueError):
        return {}


def profile_for_verified(customer_id: str | None, verified: bool) -> dict | None:
    """The verified customer's own contact details, or nothing.

    Both arguments are required for a reason: a customer id alone is not
    permission to see a profile. Anything short of a completed verification
    returns `None`, so the panel cannot become a way to look someone up.
    """
    if not verified or not customer_id:
        return None
    found = _profiles().get(customer_id)
    if not found:
        return None
    return {"customer_id": customer_id, **found}
