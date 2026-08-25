"""Deterministic fallback used when the model is unavailable or ungrounded.

This is a safety net, not the product. It is intentionally worse at language —
but it invokes exactly the same tools, so ownership scoping, the
eligibility-before-mutation gate, and the confirmation gate hold identically. The
service degrades at conversation quality without degrading at safety.
"""

from __future__ import annotations

import re

from ..models.conversation import ConversationState
from ..tools import build_toolset
from ..tools.context import ToolContext
from .state import EXCHANGE_INTENT_RE, ORDER_ID_RE, RETURN_INTENT_RE, resolve_order_id

DISCOUNT_RE = re.compile(r"\b(?:discount|coupon|promo ?code|waive|goodwill)\b", re.I)
CREDIT_RE = re.compile(r"\b(?:store credit|delay credit|compensation)\b", re.I)
ADDRESS_RE = re.compile(r"\b(?:change|update|new)\s+(?:the\s+)?(?:delivery\s+)?address\b|\baddress change\b", re.I)
POLICY_QUESTION_RE = re.compile(
    r"\b(?:policy|how long|how much|timeline|what are|what is|do you|is there|can i|when)\b", re.I
)
SIZE_RE = re.compile(r"\b(?:size\s*)?(XXL|XL|XS|S|M|L|\d{2})\b", re.I)
DAMAGE_RE = re.compile(r"\b(damaged|broken|defective|faulty|wrong item)\b", re.I)
# "When do I get my money back?" is a timing question about an order that already
# exists, not a request to raise a return. It has to reach get_refund_timing,
# which is also where policy 3.3 hands a cash-on-delivery refund to a human.
REFUND_TIMING_RE = re.compile(
    r"\b(?:when|how long|how soon|timeline|timing|how many days)\b[^?]{0,60}"
    r"\b(?:refund|money back|repaid|reimbursed)\b"
    r"|\brefund\b[^?]{0,40}\b(?:take|takes|arrive|arrives|timeline|timing|processed)\b",
    re.I,
)
SHIPPING_FEE_RE = re.compile(
    r"\b(?:shipping|delivery)\s+(?:fee|cost|costs|charge|charges)\b"
    r"|\bhow much\b[^?]{0,20}\b(?:shipping|delivery)\b"
    r"|\bexpress shipping\b",
    re.I,
)
STATUS_RE = re.compile(
    r"\b(?:status|where is|where'?s|track|tracking|delivered|delivery|arrive|arriving|"
    r"dispatch|dispatched|shipped|on its way|update on|when will it|how is)\b",
    re.I,
)
LIST_ORDERS_RE = re.compile(
    r"\b(?:my orders|all my orders|orders do i have|list (?:my )?orders|what have i ordered|"
    r"recent orders|order history)\b",
    re.I,
)

# Calibrated against covered and uncovered questions; see the note at its use.
POLICY_ANSWER_THRESHOLD = 0.60


def _reason_from(message: str) -> str:
    lowered = message.lower()
    if "damag" in lowered or "broken" in lowered:
        return "damaged"
    if "defect" in lowered or "faulty" in lowered:
        return "defective"
    if "wrong" in lowered:
        return "wrong_item"
    return "change_of_mind"


def _pick_item(order: dict, message: str) -> dict | None:
    upper = message.upper()
    lowered = message.lower()
    for item in order.get("items", []):
        if item["sku"].upper() in upper or item["name"].lower() in lowered:
            return item
        # Match on a word from the product name, so "the tee" resolves to the
        # Everyday Cotton Tee on a two-item order.
        for word in item["name"].lower().split():
            if len(word) >= 3 and word in lowered:
                return item
    items = order.get("items", [])
    return items[0] if len(items) == 1 else None


def _status_line(order: dict) -> str:
    status = order.get("status")
    oid = order.get("order_id")
    if status == "in_transit":
        return (
            f"{oid} is on its way with {order.get('carrier')}. Expected by "
            f"{order.get('expected_delivery')}; tracking {order.get('tracking_number')}."
        )
    if status == "partially_shipped":
        pending = [i for i in order.get("items", []) if not i.get("shipped", True)]
        names = ", ".join(
            f"{i['name']} (back in stock around {i.get('backorder_eta', 'soon')})" for i in pending
        )
        return (
            f"{oid} is partially shipped with {order.get('carrier')} ({order.get('tracking_number')}). "
            f"Still to come: {names}. It ships separately at no extra shipping cost."
        )
    if status == "delayed":
        return (
            f"I'm sorry — {oid} is delayed. It was expected by {order.get('expected_delivery')}. "
            "Because it is more than 3 business days late you can have the ₹250 store credit; "
            "just ask and I'll apply it. No cancellation is needed."
        )
    if status == "delivered":
        return f"{oid} was delivered on {str(order.get('delivered_at'))[:10]}."
    if status == "cancelled":
        return (
            f"{oid} was cancelled on {str(order.get('cancelled_at'))[:10]} and its refund status is "
            f"{order.get('refund_status', 'not available')}. A return can't be raised on a cancelled order."
        )
    return f"The status of {oid} is {status}."


CUSTOMER_ID_RE = re.compile(r"\bC-\d{3}\b", re.I)

# Format hints only. These are deliberately values that exist in no account, so
# the prompt cannot hand a caller a working identifier to try.
ASK_BOTH = (
    "Sure — could you give me your customer ID and the order ID, so I can verify the order? "
    "They look like C-000 and TR-0000."
)
ASK_CUSTOMER = "Thanks. Could you also give me your customer ID? It looks like C-000."
ASK_ORDER = "Thanks. Which order is this about? The order ID looks like TR-0000."


def _verification_turn(message: str, call, state: ConversationState) -> str:
    """Collect and submit identifiers until the application verifies them."""
    customer_match = CUSTOMER_ID_RE.search(message)
    order_match = ORDER_ID_RE.search(message)

    if not customer_match and not order_match:
        return ASK_BOTH

    # One identifier seen is not an authorisation — it only moves the
    # lifecycle far enough to record that we are mid-collection.
    state.mark_identifiers_collected()

    if not customer_match:
        state.active_order_id = order_match.group(0).upper()
        return ASK_CUSTOMER
    if not order_match:
        if not state.active_order_id:
            return ASK_ORDER
        order_match_value = state.active_order_id
    else:
        order_match_value = order_match.group(0).upper()

    result = call(
        "verify_identity",
        customer_id=customer_match.group(0).upper(),
        order_id=order_match_value,
    )
    if result.get("authorized"):
        return (
            f"Thanks — I've verified order {result['order_id']} on your account. "
            "What would you like to do with it?"
        )

    code = result.get("reason_code")
    if code == "CUSTOMER_NOT_RECOGNISED":
        return "I couldn't verify that customer ID. Please check it and try again."
    if code == "IDENTITY_LOCKED":
        return (
            "I can only continue with the account this conversation was already verified for. "
            "Please start a new conversation to use a different account."
        )
    return (
        "I can only help with orders associated with the verified customer account. "
        "Please check the customer ID and order ID and try again."
    )


def fallback_reply(message: str, ctx: ToolContext, state: ConversationState) -> str:
    tools = {t.name: t for t in build_toolset(ctx)}

    def call(name: str, **kwargs) -> dict:
        return tools[name].invoke(kwargs)

    # 0. Nothing order-specific happens before the conversation is verified.
    #
    # On the model path the ids are extracted by the model and validated here.
    # With no model there is nothing to do the extraction, so this degraded path
    # falls back to matching the id formats. It is deliberately literal: it only
    # ever *submits* candidates to verify_identity, which does the deciding.
    if not ctx.is_verified:
        return _verification_turn(message, call, state)

    pending = ctx.auth.incoming

    # 1. An outstanding offer the customer has just agreed to.
    if pending and ctx.auth.user_confirmed:
        call("get_order", order_id=pending.order_id)
        if pending.kind == "return":
            check = call(
                "check_return_eligibility",
                order_id=pending.order_id,
                item_id=pending.item_id,
                reason=pending.reason,
            )
            if not check.get("eligible"):
                ctx.auth.clear_pending()
                return f"I can't create that return: {check.get('reason')}"
            result = call(
                "initiate_return",
                order_id=pending.order_id,
                item_id=pending.item_id,
                reason=pending.reason,
            )
            if not result.get("created"):
                return "I couldn't complete that return safely, so I'd rather a person check it."
            return (
                f"Your return is created — reference {result['reference']}. "
                "You'll be prompted to pick a free reverse-pickup window."
            )

        check = call(
            "check_exchange_eligibility",
            order_id=pending.order_id,
            item_id=pending.item_id,
            requested_size=pending.requested_size,
        )
        if not check.get("eligible"):
            ctx.auth.clear_pending()
            return f"I can't create that exchange: {check.get('reason')}"
        result = call(
            "initiate_exchange",
            order_id=pending.order_id,
            item_id=pending.item_id,
            requested_size=pending.requested_size,
        )
        if not result.get("created"):
            return "I couldn't complete that exchange safely, so I'd rather a person check it."
        return (
            f"Your size exchange is created — reference {result['reference']}. "
            "Availability is still to be confirmed; if the size is unavailable it converts to a refund."
        )

    order_id = resolve_order_id(message, state)

    # 2. "What orders do I have?" — scoped to the trusted customer by the tool.
    # Checked before the active order is used, so asking for the list still
    # lists even when a specific order is already in play.
    if LIST_ORDERS_RE.search(message):
        listing = call("list_my_orders")
        if not listing["orders"]:
            return "I can't see any orders on your account."
        lines = "\n".join(
            f"- **{o['order_id']}** — {o['status'].replace('_', ' ')} ({', '.join(o['items'])})"
            for o in listing["orders"]
        )
        return f"Here are the orders on your account:\n\n{lines}\n\nWhich one can I help with?"

    # 3. The one compensation the agent may grant.
    if CREDIT_RE.search(message) and order_id:
        call("get_order", order_id=order_id)
        credit = call("issue_delay_credit", order_id=order_id)
        if credit.get("created"):
            return (
                f"I've applied the ₹{credit['detail']['amount']} delay store credit to your account "
                f"({credit['reference']}). No cancellation needed."
            )
        return f"I can't apply a delay credit to {order_id}: {credit.get('message')}"

    # 3. Anything else framed as compensation is outside the agent's authority.
    if DISCOUNT_RE.search(message):
        result = call(
            "escalate_to_human",
            reason="unauthorized_discount_request",
            summary="Customer asked for a discount, coupon, waiver, or goodwill credit outside policy authority.",
            order_id=order_id,
            required_human_action="Decide whether any goodwill applies; the assistant has no such authority.",
        )
        return (
            "I can't offer discounts, coupons, waivers, or goodwill credits that aren't in Trendly's "
            f"policy, so I've passed this to a human agent ({result['case_reference']})."
        )

    lookup = call("get_order", order_id=order_id) if order_id else {"found": False}
    order = lookup.get("order")
    if order_id and not order:
        return (
            "I can't find that order on your account. Please check the order ID, or sign in with the "
            "account used for the purchase."
        )

    if order:
        if order.get("status") == "lost_in_transit":
            summary = (
                f"Lost-parcel claim for {order['order_id']} ({order.get('carrier')} tracking "
                f"{order.get('tracking_number')}). Customer needs a replacement or a full refund."
            )
            result = call(
                "escalate_to_human",
                reason="lost_parcel",
                summary=summary,
                order_id=order["order_id"],
                required_human_action=(
                    "Offer the customer a free replacement or a full refund, their choice, "
                    "and resolve within 5 business days (policy 1.6)."
                ),
            )
            return (
                f"I'm sorry — {order['order_id']} has been marked lost by {order.get('carrier')}. "
                "That's a lost-parcel claim rather than a return, so I've sent it to a specialist "
                f"({result['case_reference']}). They can arrange a free replacement or a full refund."
            )

        if ADDRESS_RE.search(message):
            if order.get("status") in {"in_transit", "partially_shipped", "delayed", "delivered"}:
                return (
                    "This order has already been dispatched, so its delivery address can't be changed. "
                    "Please refuse delivery and place a new order if you need it sent elsewhere."
                )
            return "An address can be changed only before dispatch. Let me check the fulfilment state first."

        if order.get("status") == "cancelled":
            return _status_line(order)

        # Timing and charges are joins against this order, resolved before the
        # return branch so "when is my refund?" is not read as "raise a return".
        if REFUND_TIMING_RE.search(message):
            timing = call("get_refund_timing", order_id=order["order_id"])
            if not timing.get("known"):
                return (
                    "I can't work out the refund route for this order, so I've passed it to a "
                    "human support agent."
                )
            base = (
                f"Your refund goes to {timing['refund_destination']} and takes "
                f"{timing['refund_window']} after warehouse inspection "
                f"({timing['inspection_window']}) — policy 3.1."
            )
            if timing.get("requires_human"):
                return (
                    f"{base} Because this order was cash on delivery, a colleague will contact you "
                    "through a secure link to collect your bank details — I can't take those in "
                    f"chat (case {timing['case_reference']})."
                )
            return base

        if SHIPPING_FEE_RE.search(message):
            mode = "express" if "express" in message.lower() else "standard"
            quote = call("quote_shipping_fee", order_id=order["order_id"], shipping_mode=mode)
            if not quote.get("available"):
                return f"{quote.get('reason')} (policy 1.3)"
            fee = quote["fee"]
            charge = (
                "There is no shipping charge on this order"
                if fee == 0
                else f"Shipping on this order is Rs {fee:.0f}"
            )
            return f"{charge} — {quote['reason']} (policy 1.3)"

        wants_return = RETURN_INTENT_RE.search(message) or DAMAGE_RE.search(message)
        wants_exchange = EXCHANGE_INTENT_RE.search(message)
        # A bare item name answers the question we asked on the previous turn.
        if state.awaiting_item_for == "return" and not wants_exchange:
            wants_return = True
        elif state.awaiting_item_for == "exchange" and not wants_return:
            wants_exchange = True

        if wants_return:
            item = _pick_item(order, message)
            if not item:
                state.awaiting_item_for = "return"
                names = ", ".join(i["name"] for i in order.get("items", []))
                return f"Which item from {order['order_id']} do you mean — {names}?"
            state.awaiting_item_for = None
            check = call(
                "check_return_eligibility",
                order_id=order["order_id"],
                item_id=item["sku"],
                reason=_reason_from(message),
            )
            if not check.get("eligible"):
                return f"I can't create a return for {item['name']}: {check.get('reason')}"
            conditions = " ".join(check.get("conditions", []))
            return (
                f"{item['name']} is eligible for return. {conditions} "
                'Reply "confirm" and I\'ll create it.'
            )

        if wants_exchange:
            item = _pick_item(order, message)
            if not item:
                state.awaiting_item_for = "exchange"
                return f"Which item from {order['order_id']} would you like to exchange?"
            state.awaiting_item_for = None
            kind = (
                "colour"
                if re.search(r"colou?r", message, re.I)
                else "style" if "style" in message.lower() else "size"
            )
            size_match = SIZE_RE.search(message)
            check = call(
                "check_exchange_eligibility",
                order_id=order["order_id"],
                item_id=item["sku"],
                requested_size=size_match.group(1).upper() if size_match else None,
                exchange_kind=kind,
            )
            if not check.get("eligible"):
                if check.get("needs_human"):
                    result = call(
                        "escalate_to_human",
                        reason="second_exchange",
                        summary=f"Second exchange requested for {order['order_id']} / {item['sku']}; policy 4.4 needs human approval.",
                        order_id=order["order_id"],
                        required_human_action="Review and approve or deny a second exchange on this item (policy 4.4).",
                    )
                    return f"{check['reason']} I've passed it to a human agent ({result['case_reference']})."
                return f"I can't create that exchange: {check.get('reason')}"
            size = size_match.group(1).upper() if size_match else "the requested size"
            return (
                f"{item['name']} is eligible for a size exchange to {size}, subject to availability. "
                'Reply "confirm" and I\'ll create it.'
            )

        # Status is the right default for anything about this order — including a
        # bare mention of it ("and what about TR-4527?"). What must *not* happen
        # is answering a general question with a tracking number, so a question
        # that names no order and asks about Trendly at large falls through to
        # the policy and escalation path below.
        asks_generally = (
            not ORDER_ID_RE.search(message)
            and POLICY_QUESTION_RE.search(message)
            and not STATUS_RE.search(message)
        )
        if not asks_generally:
            return _status_line(order)

    # 4. Not an order-specific request: answer from the policy document, or hand off.
    #
    # Retrieval always returns its nearest passage, and relevance scores for
    # covered and uncovered questions overlap — "do you ship to Antarctica"
    # shares vocabulary with the shipping section. Judging coverage is a
    # semantic call, which on the primary path the model makes (backed by the
    # dead-end guard). Without a model there is no one to make it, so this
    # degraded path answers only on a confident match and escalates otherwise.
    # Sending a real question to a human is a worse experience; quoting the
    # wrong rule is a wrong answer.
    if POLICY_QUESTION_RE.search(message) or RETURN_INTENT_RE.search(message) or EXCHANGE_INTENT_RE.search(message):
        result = call("search_policy", query=message)
        passages = result.get("passages") or []
        # Abstain outright when the document does not use the words the question
        # is about — no similarity score redeems that. Offline this is the whole
        # judgement; with a model in front it is guidance the model weighs.
        silent = bool(result.get("unsupported_terms"))
        if not silent and passages and passages[0]["score"] >= POLICY_ANSWER_THRESHOLD:
            top = passages[0]
            return (
                f"Here's what Trendly's policy says (section {top['section_number']}, "
                f"{top['section_title']}):\n\n{top['text']}"
            )

    result = call(
        "escalate_to_human",
        reason="policy_not_covered",
        summary=f"Customer asked something the Trendly policy does not cover: {message!r}",
        order_id=order_id,
        required_human_action="Answer from a source outside the shipping and returns policy, or tell the customer it is not offered.",
    )
    return (
        "I don't have a policy-backed answer for that, so I've passed it to a human support agent "
        f"({result['case_reference']}). They'll follow up with you."
    )
