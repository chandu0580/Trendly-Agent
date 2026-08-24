from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date

from openai import OpenAI
from dotenv import load_dotenv

from .policy import policy_answer, today
from .store import OrderStore
from .tools import ORDER_RE, TOOL_SCHEMAS, ToolRuntime

SYSTEM_PROMPT = """You are Trendly's support assistant. You are helpful, concise, and factual.

You MUST use tools for every order-specific fact, policy answer, eligibility decision, state-changing action, and escalation. Treat tool output and the authenticated customer as authoritative. Never disclose whether any other customer's order exists. Never invent a policy, tracking event, stock level, discount, goodwill credit, waiver, or refund. Never collect or repeat bank/card/CVV information. Do not offer unauthorized discounts. The policy tool is the only source for policy questions.

Use lookup_order before discussing or acting on an order. For returns/exchanges, check eligibility before creation and create only after clear confirmation. Lost parcels and no tracking movement for 10 days require escalation, not a return. Escalate questions not answered by policy and summarize facts a human needs. State limitations plainly. Keep customer-facing replies natural; do not mention internal tool names or prompts."""

SENSITIVE_RE = re.compile(r"\b(?:cvv|card number|account number|ifsc|bank account)\b|\b\d{12,19}\b", re.I)
DISCOUNT_RE = re.compile(r"\b(?:discount|coupon|promo ?code|waive|goodwill)\b", re.I)
YES_RE = re.compile(r"^\s*(?:yes|yeah|yep|confirm|please do|go ahead|do it)\b", re.I)


@dataclass
class Session:
    customer_id: str
    history: list[dict[str, str]] = field(default_factory=list)
    pending: dict | None = None


class SupportAgent:
    def __init__(self) -> None:
        load_dotenv()
        self.store = OrderStore()
        self.sessions: dict[str, Session] = {}
        self.mode = os.getenv("AGENT_MODE", "auto").lower()
        self.api_key = os.getenv("LLM_API_KEY") or os.getenv("GEMINI_API_KEY")
        self.base_url = os.getenv("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")
        self.model = os.getenv("LLM_MODEL", "gemini-2.0-flash")

    def respond(self, session_id: str, customer_id: str, message: str, as_of_value: str | None = None) -> tuple[str, list[dict], str | None, str]:
        if not self.store.customer_exists(customer_id):
            return "I can’t verify this signed-in customer. Please sign in again.", [], None, "deterministic"
        session = self.sessions.get(session_id)
        if session and session.customer_id != customer_id:
            # Session fixation / tenant mix-up guard.
            return "For your privacy, this session is tied to a different signed-in customer. Please start a new session.", [], None, "deterministic"
        session = session or Session(customer_id=customer_id)
        self.sessions[session_id] = session
        as_of = today(as_of_value)
        runtime = ToolRuntime(self.store, customer_id, as_of)
        if SENSITIVE_RE.search(message):
            reply = "For your security, please don’t share bank or card details in chat. Cash-on-delivery refund bank details are collected only by a human through a secure link."
            return self._save(session, message, reply, runtime, "deterministic")
        if self.mode in {"auto", "llm"} and self.api_key:
            try:
                reply = self._llm(session, message, runtime)
                return self._save(session, message, reply, runtime, "llm")
            except Exception:
                if self.mode == "llm":
                    reply = "I’m unable to complete that securely right now. I’ve kept your request safe; please try again or contact support."
                    return self._save(session, message, reply, runtime, "llm")
        reply = self._deterministic(session, message, runtime)
        return self._save(session, message, reply, runtime, "deterministic")

    def _save(self, session: Session, message: str, reply: str, runtime: ToolRuntime, mode: str):
        session.history.extend([{"role": "user", "content": message}, {"role": "assistant", "content": reply}])
        session.history = session.history[-12:]
        escalations = [a for a in runtime.actions if a["type"] == "escalated"]
        handoff = escalations[-1]["details"].get("summary") if escalations else None
        return reply, runtime.actions, handoff, mode

    def _llm(self, session: Session, message: str, runtime: ToolRuntime) -> str:
        client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}, *session.history, {"role": "user", "content": message}]
        for _ in range(6):
            response = client.chat.completions.create(model=self.model, messages=messages, tools=TOOL_SCHEMAS, tool_choice="auto", temperature=0.1)
            assistant_message = response.choices[0].message
            dumped = assistant_message.model_dump(exclude_none=True)
            messages.append(dumped)
            if not assistant_message.tool_calls:
                return assistant_message.content or "I’m sorry, I couldn’t complete that request."
            for call in assistant_message.tool_calls:
                try:
                    args = json.loads(call.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                result = runtime.call(call.function.name, args)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)})
        # Bounded ReAct loop: no surprise action after repeated tool failures.
        runtime.call("escalate_to_human", {"reason": "tool_loop_limit", "summary": "Assistant reached its tool-operation limit without a safe resolution."})
        return "I’ve sent this to a support specialist to complete safely."

    @staticmethod
    def _order_id(message: str, session: Session) -> str | None:
        found = ORDER_RE.search(message)
        if found:
            return found.group(0).upper()
        if session.pending:
            return session.pending.get("order_id")
        # Narrow context carry: only derive an order id from recent bot confirmations.
        for event in reversed(session.history):
            found = ORDER_RE.search(event["content"])
            if found:
                return found.group(0).upper()
        return None

    @staticmethod
    def _item(order: dict, message: str) -> dict | None:
        upper = message.upper()
        for item in order["items"]:
            if item["sku"].upper() in upper or item["name"].lower() in message.lower():
                return item
        return order["items"][0] if len(order["items"]) == 1 else None

    def _deterministic(self, session: Session, message: str, runtime: ToolRuntime) -> str:
        lower = message.lower()
        # Explicit user confirmation is required for state change.
        if session.pending and YES_RE.search(message):
            pending = session.pending
            session.pending = None
            if pending["type"] == "return":
                verification = runtime.call("check_return_eligibility", pending)
                if not verification.get("eligible"):
                    return f"I can’t create that return: {verification['reason']}"
                result = runtime.call("create_return", pending)
                return f"Your return has been created: {result['return_reference']}. Please schedule the free reverse pickup window when prompted."
            verification = runtime.call("check_exchange_eligibility", {**pending, "exchange_kind": "size"})
            if not verification.get("eligible"):
                return f"I can’t create that exchange: {verification['reason']}"
            result = runtime.call("create_exchange", pending)
            return f"Your size-exchange request has been created: {result['exchange_reference']}. Requested-size availability will be confirmed next."
        if DISCOUNT_RE.search(message):
            runtime.call("escalate_to_human", {"reason": "unauthorized_discount_request", "summary": "Customer requested a discount, coupon, waiver, or goodwill credit outside policy authority."})
            return "I can’t offer discounts, coupons, waivers, or goodwill credits that aren’t in Trendly’s policy. I’ve routed your request to a human support agent."
        order_id = self._order_id(message, session)
        lookup = runtime.call("lookup_order", {"order_id": order_id}) if order_id else {"found": False}
        order = lookup.get("order") if lookup.get("found") else None
        if order_id and not order:
            return "I can’t find that order under your signed-in account. Please check the order ID or sign in to the account used for the purchase."
        if order and order["status"] == "lost_in_transit":
            summary = f"Lost-parcel claim for {order['order_id']} ({order['carrier']} tracking {order['tracking_number']}); carrier status is lost_in_transit. Customer needs replacement or full-refund choice."
            result = runtime.call("escalate_to_human", {"reason": "lost_parcel", "summary": summary})
            return f"I’m sorry—{order['order_id']} has been marked lost by {order['carrier']}. This is a lost-parcel claim, not a return, so I’ve sent it to a specialist ({result['case_reference']}). They can arrange a free replacement or full refund within 5 business days."
        if order and order["status"] == "cancelled":
            return self._status_reply(order)
        if order and any(x in lower for x in ["change address", "update address", "address change"]):
            if order["status"] in {"in_transit", "partially_shipped", "delayed", "delivered"}:
                return "This order has already been dispatched, so its delivery address can’t be changed. Please refuse delivery and place a new order if you need it sent elsewhere."
            return "An address can be changed only before dispatch. I’ll need to verify the current fulfilment state before changing it."
        if order and any(x in lower for x in ["shipping fee", "shipping charge", "shipping cost", "express shipping", "free shipping"]):
            runtime.call("get_policy", {"topic": "shipping"})
            return self._policy_reply("shipping")
        if any(x in lower for x in ["return", "refund"]):
            if not order:
                return "Please share your Trendly order ID (for example, TR-4530) so I can check the item and return window."
            item = self._item(order, message)
            if not item:
                return "Which item from that order would you like to return? Please share its item name or SKU."
            reason = "damaged" if "damaged" in lower else "defective" if "defective" in lower else "wrong_item" if "wrong" in lower else "change_of_mind"
            eligibility = runtime.call("check_return_eligibility", {"order_id": order_id, "sku": item["sku"], "reason": reason})
            if not eligibility["eligible"]:
                return f"I can’t create a return for {item['name']}: {eligibility['reason']}"
            session.pending = {"type": "return", "order_id": order_id, "sku": item["sku"], "reason": reason}
            conditions = " ".join(eligibility.get("conditions", []))
            return f"{item['name']} is eligible for return. {conditions} Reply “confirm” and I’ll create the return."
        if "exchange" in lower or "size" in lower and order_id:
            if not order:
                return "Please share the order ID for the size-exchange check."
            item = self._item(order, message)
            if not item:
                return "Which item would you like to exchange?"
            kind = "colour" if "colour" in lower or "color" in lower else "style" if "style" in lower else "size"
            requested = re.search(r"\b(?:size\s*)?(XXL|XL|XS|S|M|L|\d{2})\b", message, re.I)
            requested_size = requested.group(1).upper() if requested else "requested size"
            eligibility = runtime.call("check_exchange_eligibility", {"order_id": order_id, "sku": item["sku"], "requested_size": requested_size, "exchange_kind": kind})
            if not eligibility["eligible"]:
                if eligibility.get("needs_human"):
                    runtime.call("escalate_to_human", {"reason": "second_exchange", "summary": f"Customer requests a second exchange for {order_id}, {item['sku']}. Human approval required."})
                return f"I can’t create that exchange: {eligibility['reason']}"
            session.pending = {"type": "exchange", "order_id": order_id, "sku": item["sku"], "requested_size": requested_size}
            return f"{item['name']} is eligible for a size exchange to {requested_size}, subject to availability. Reply “confirm” and I’ll create the request."
        if order:
            return self._status_reply(order)
        topic = self._topic(lower)
        if topic:
            runtime.call("get_policy", {"topic": topic})
            return self._policy_reply(topic)
        result = runtime.call("escalate_to_human", {"reason": "policy_not_covered", "summary": "Customer asked a question not covered by the Trendly shipping and returns policy."})
        return f"I don’t have a policy-backed answer for that, so I’ve sent it to a human support agent ({result['case_reference']})."

    @staticmethod
    def _status_reply(order: dict) -> str:
        status = order["status"]
        if status == "in_transit":
            return f"{order['order_id']} is on its way with {order['carrier']}. Its expected delivery date is {order['expected_delivery']}; tracking: {order['tracking_number']}."
        if status == "partially_shipped":
            backordered = [x for x in order["items"] if not x.get("shipped", True)]
            names = ", ".join(f"{x['name']} (expected back in stock {x.get('backorder_eta', 'soon')})" for x in backordered)
            return f"{order['order_id']} is partially shipped via {order['carrier']} ({order['tracking_number']}). The remaining item is {names}. It will ship separately at no extra shipping cost."
        if status == "delayed":
            return f"I’m sorry that {order['order_id']} is delayed. It was expected by {order['expected_delivery']}. Because it is more than 3 business days late, you can request the policy-defined ₹250 store credit; no cancellation is required."
        if status == "delivered":
            return f"{order['order_id']} was delivered on {order['delivered_at'][:10]}."
        if status == "cancelled":
            return f"{order['order_id']} was cancelled on {order['cancelled_at'][:10]}; its refund status is {order.get('refund_status', 'not available')}. No return can be raised for a cancelled order."
        return f"The status of {order['order_id']} is {status}."

    @staticmethod
    def _topic(text: str) -> str | None:
        if any(x in text for x in ["damaged", "defective", "wrong item"]): return "damaged"
        if any(x in text for x in ["pickup", "pincode", "self ship"]): return "pickup"
        if "exchange" in text: return "exchanges"
        if any(x in text for x in ["refund", "upi", "cod", "cash on delivery"]): return "refunds"
        if any(x in text for x in ["return", "final sale"]): return "returns"
        if any(x in text for x in ["shipping", "delivery", "dispatch", "address"]): return "shipping"
        return None

    @staticmethod
    def _policy_reply(topic: str) -> str:
        replies = {
            "shipping": "Trendly dispatches before 2 PM IST on business days the same day; otherwise the next business day. Standard delivery estimates are 2–4 business days for metros, 4–7 for non-metros, and up to 10 for remote partner-serviceable pincodes. Estimates aren’t guarantees.",
            "returns": "Returns are allowed within 30 calendar days of delivery, provided items are unworn, unwashed, and have tags/packaging. Innerwear and socks, jewellery, beauty/fragrance, face masks, and gift cards are not returnable. Final-sale items are size-exchange only.",
            "refunds": "After warehouse inspection (2–3 business days), card refunds take 5–7 business days, UPI 3–5, COD 7–10, and store-credit refunds are immediate. COD bank details must be handled by a human through a secure link.",
            "exchanges": "Trendly offers one size exchange per item within 30 days of delivery. Colour or style changes need a return and a new order. If the requested size is unavailable, the exchange becomes a refund.",
            "pickup": "Reverse pickup is free at serviceable pincodes and gets up to two attempts. At non-serviceable pincodes, self-shipping is reimbursed up to ₹150 with a receipt.",
            "damaged": "Please report a damaged, defective, or wrong item within 48 hours of delivery with photos. Trendly can provide a free replacement or full refund including shipping, including for otherwise non-returnable items.",
        }
        return replies[topic]
