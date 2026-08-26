"""Session state and the guards that inspect a turn before it reaches the customer.

The guards here are not an intent router — they never choose an answer. Each can
only do one of two things: send the model back for another grounded tool round,
or hand the turn to the deterministic fallback. That asymmetry is what makes them
safe to key off imprecise signals like message text.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field

from ..models.conversation import ConversationState, PendingAction
from ..tools.context import ToolContext

# Accepts the separators people actually type — "TR-4524", "TR 4524", "TR4524" —
# and the en/em dash autocorrect substitutes for a hyphen. The repository
# normalises further, so anything matched here resolves to a real order.
ORDER_ID_RE = re.compile(r"\bTR[-–— ]?\d{4}\b", re.I)

# Catches a customer *offering* financial credentials as well as pasting them.
# "Can I give you my bank details here?" carries no digits and none of the
# formal terms, but it is exactly the moment to refuse.
#
# Scoped to avoid over-blocking ordinary policy questions: "how long does a COD
# refund take" and "what payment methods do you support" contain none of these.
SENSITIVE_RE = re.compile(
    # Named credentials, always.
    r"\b(?:cvv|cvc|ifsc|sort code|routing number|iban|swift code)\b"
    # "<financial thing> number/details" — covers "bank details", "card number",
    # "banking details", "payment details".
    r"|\b(?:card|account|bank|banking|payment)\s+(?:number|numbers|details|detail|info|information|credentials)\b"
    r"|\bbank\s+account\b"
    # Offering them, which is the moment to refuse even with nothing pasted yet.
    r"|\b(?:give|send|share|provide|pass|take)\b[^.?!]{0,20}\bmy\s+(?:bank|card|account|payment)\b"
    # A raw long number is a credential regardless of wording.
    r"|\b\d{12,19}\b",
    re.I,
)

RETURN_INTENT_RE = re.compile(r"\b(?:return|refund|money back|send (?:it|this|them) back)\b", re.I)
EXCHANGE_INTENT_RE = re.compile(
    r"\b(?:exchange|swap|different size|another size|bigger|smaller)\b", re.I
)

# Narrow on purpose: "I can't create a return for these earrings, jewellery is
# non-returnable" is a grounded answer, not a failure to answer, and escalating
# it would be wrong.
DEAD_END_RE = re.compile(
    r"(?:don'?t|do not|doesn'?t|does not|can'?t|cannot|unable to)\s+(?:\w+\s+){0,3}"
    r"(?:cover|covered|address|answer|help with|find (?:a|any) policy|have (?:a|any) policy)"
    r"|\bnot covered by (?:the |our )?polic"
    r"|\bpolicy (?:is )?silent\b"
    r"|\bno policy (?:source|information|guidance)\b",
    re.I,
)

# Terms that oblige a grounding call before any customer-facing claim.
GROUNDED_TOPIC_RE = re.compile(
    r"\b(?:return|refund|exchange|track|tracking|status|deliver|delivery|address|cancel|cancelled|"
    r"damaged|defective|wrong item|ship|shipping|pickup|policy|timeline|order|credit|coupon|discount)\b",
    re.I,
)


@dataclass
class SessionStore:
    """In-process conversation store, keyed by session id.

    Distinct sessions never share state. Two requests on the *same* session
    would otherwise interleave a read-modify-write on one `ConversationState` —
    verification, active order, and the pending proposal are all read, decided
    on, and written back within a turn — so a per-session lock serialises them.

    Deliberately in-process: this prototype has no shared store, and a restart
    forgets sessions. Documented rather than solved with infrastructure.
    """

    sessions: dict[str, ConversationState] = field(default_factory=dict)
    _locks: dict[str, threading.Lock] = field(default_factory=dict)
    _registry_lock: threading.Lock = field(default_factory=threading.Lock)

    def lock_for(self, session_id: str) -> threading.Lock:
        """One lock per session, created once."""
        with self._registry_lock:
            return self._locks.setdefault(session_id, threading.Lock())

    def get(self, session_id: str, customer_id: str) -> ConversationState | None:
        return self.sessions.get(session_id)

    def start(self, session_id: str, customer_id: str) -> ConversationState:
        state = ConversationState(session_id=session_id, customer_id=customer_id)
        self.sessions[session_id] = state
        return state

    def clear(self) -> None:
        self.sessions.clear()
        with self._registry_lock:
            self._locks.clear()


def contains_sensitive_data(message: str) -> bool:
    return bool(SENSITIVE_RE.search(message))


def needs_grounding(message: str, pending: PendingAction | None = None) -> bool:
    if pending is not None:
        return True
    return bool(ORDER_ID_RE.search(message) or GROUNDED_TOPIC_RE.search(message))


def unchecked_eligibility(message: str, ctx: ToolContext) -> list[str]:
    """Eligibility claims the model is about to make without a verdict behind them.

    Escalations are exempt: a lost parcel is not an eligibility question, and
    forcing a check there would contradict the lost-parcel rule.
    """
    if not ctx.auth.found_orders or ctx.escalations:
        return []
    missing: list[str] = []
    if RETURN_INTENT_RE.search(message) and "check_return_eligibility" not in ctx.trace:
        missing.append("check_return_eligibility")
    if EXCHANGE_INTENT_RE.search(message) and "check_exchange_eligibility" not in ctx.trace:
        missing.append("check_exchange_eligibility")
    return missing


# A reference the customer can quote back: case, return, exchange, credit.
#
# Deliberately prefix-agnostic. Listing them by hand missed `CRD-` (the ledger's
# credit prefix) while matching `CR-`, so a fabricated store-credit reference —
# the one kind involving money — went unchecked. Shape, not vocabulary: two to
# four capitals, a hyphen, then at least six alphanumerics. Verified against the
# dataset that order ids (`TR-4524`), SKUs (`TR-JNS-021`), and tracking numbers
# (`DL5519002244`) cannot match, so a new action type is covered automatically.
REFERENCE_RE = re.compile(r"\b[A-Z]{2,4}-[A-Z0-9]{6,}\b")


def fabricated_references(content: str | None, ctx: ToolContext) -> list[str]:
    """Reference numbers in the reply that no tool actually issued.

    A model that says "I've passed this to our team — case ESC-9C2A1F5D" without
    calling `escalate_to_human` has invented a ticket. The customer leaves
    believing a colleague will follow up and nothing exists to follow up. That is
    worse than any refusal, and unlike a wrong fact it cannot be walked back:
    the reference is quoted in the next conversation and matches nothing.

    Checked against the references the turn's tools genuinely returned, so this
    stays true whatever new action types are added later.
    """
    if not content:
        return []
    issued = {a["reference"].upper() for a in ctx.actions if a.get("reference")}
    return sorted({r.upper() for r in REFERENCE_RE.findall(content)} - issued)


def is_out_of_scope(message: str, ctx: ToolContext) -> bool:
    """The message is not a Trendly support request at all.

    Three signals have to agree, because the distinction being drawn is narrow:
    a question Trendly *could* answer but the policy omits ("do you offer gift
    wrapping?") needs a human, while one nobody at Trendly should answer ("what
    is Python?") needs a polite decline and no case.

    Naming an order, or touching any support topic, settles it as in scope. So
    does having searched the policy: if the agent went looking for an answer in
    Trendly's document, it judged the question a Trendly one, and a dead end
    after that is exactly what escalation exists for.
    """
    if ORDER_ID_RE.search(message) or GROUNDED_TOPIC_RE.search(message):
        return False
    return "search_policy" not in ctx.trace and not ctx.policy_sections


def is_dead_end(content: str | None, ctx: ToolContext) -> bool:
    """A reply that admits defeat without handing the customer to a person."""
    if not content or ctx.escalations:
        return False
    return bool(DEAD_END_RE.search(content))


def resolve_order_id(message: str, state: ConversationState) -> str | None:
    """Carry the active order forward so "can I return it?" stays on topic."""
    found = ORDER_ID_RE.search(message)
    if found:
        return found.group(0).upper()
    if state.pending_action:
        return state.pending_action.order_id
    return state.active_order_id
