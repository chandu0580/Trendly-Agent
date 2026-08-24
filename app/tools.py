from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid4

from .policy import exchange_eligibility, policy_answer, return_eligibility
from .store import OrderStore


ORDER_RE = re.compile(r"\bTR-\d{4}\b", re.I)

TOOL_SCHEMAS = [
    {"type": "function", "function": {"name": "lookup_order", "description": "Look up the authenticated customer's order. Use before discussing its status or acting on it.", "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "get_policy", "description": "Retrieve the only allowed policy source for a policy question. Never answer undocumented policy from memory.", "parameters": {"type": "object", "properties": {"topic": {"type": "string", "enum": ["shipping", "returns", "refunds", "exchanges", "pickup", "damaged"]}}, "required": ["topic"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "check_return_eligibility", "description": "Evaluate return eligibility from verified order data and policy. Must lookup order first.", "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}, "sku": {"type": "string"}, "reason": {"type": "string", "enum": ["change_of_mind", "damaged", "defective", "wrong_item"]}}, "required": ["order_id", "sku", "reason"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "create_return", "description": "Create a return only after check_return_eligibility returned eligible=true and the customer confirms the action.", "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}, "sku": {"type": "string"}, "reason": {"type": "string"}}, "required": ["order_id", "sku", "reason"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "check_exchange_eligibility", "description": "Check a requested size exchange using verified order data. Must lookup order first.", "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}, "sku": {"type": "string"}, "requested_size": {"type": "string"}, "exchange_kind": {"type": "string", "enum": ["size", "colour", "style"]}}, "required": ["order_id", "sku", "requested_size", "exchange_kind"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "create_exchange", "description": "Create an eligible size exchange only after the customer confirms. Never invent inventory availability.", "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}, "sku": {"type": "string"}, "requested_size": {"type": "string"}}, "required": ["order_id", "sku", "requested_size"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "escalate_to_human", "description": "Escalate a lost parcel, unanswerable policy question, request outside authority, or exception. Include a concise factual summary.", "parameters": {"type": "object", "properties": {"reason": {"type": "string"}, "summary": {"type": "string"}}, "required": ["reason", "summary"], "additionalProperties": False}}},
]


@dataclass
class ToolRuntime:
    store: OrderStore
    customer_id: str
    as_of: date
    actions: list[dict] = field(default_factory=list)
    checked_returns: set[tuple[str, str]] = field(default_factory=set)
    checked_exchanges: set[tuple[str, str]] = field(default_factory=set)

    def _order(self, order_id: str) -> dict | None:
        return self.store.get_order_for_customer(order_id, self.customer_id)

    def call(self, name: str, args: dict) -> dict:
        if name == "lookup_order":
            order = self._order(args["order_id"])
            if not order:
                return {"found": False, "message": "No matching order is available for this signed-in customer."}
            return {"found": True, "order": order}
        if name == "get_policy":
            return policy_answer(args["topic"])
        if name == "check_return_eligibility":
            order = self._order(args["order_id"])
            if not order:
                return {"eligible": False, "reason": "Order not found for this customer."}
            result = return_eligibility(order, args["sku"], self.as_of, args["reason"])
            self.checked_returns.add((args["order_id"].upper(), args["sku"].upper()))
            return result
        if name == "create_return":
            key = (args["order_id"].upper(), args["sku"].upper())
            if key not in self.checked_returns:
                return {"created": False, "message": "Safety check failed: eligibility must be checked first."}
            ref = f"RET-{uuid4().hex[:8].upper()}"
            action = {"type": "return_created", "reference": ref, "details": {**args, "pickup": "pending_schedule"}}
            self.actions.append(action)
            return {"created": True, "return_reference": ref, "pickup": "pending_schedule"}
        if name == "check_exchange_eligibility":
            order = self._order(args["order_id"])
            if not order:
                return {"eligible": False, "reason": "Order not found for this customer."}
            result = exchange_eligibility(order, args["sku"], self.as_of, args["exchange_kind"])
            self.checked_exchanges.add((args["order_id"].upper(), args["sku"].upper()))
            return result
        if name == "create_exchange":
            key = (args["order_id"].upper(), args["sku"].upper())
            if key not in self.checked_exchanges:
                return {"created": False, "message": "Safety check failed: eligibility must be checked first."}
            ref = f"EXC-{uuid4().hex[:8].upper()}"
            action = {"type": "exchange_created", "reference": ref, "details": args}
            self.actions.append(action)
            return {"created": True, "exchange_reference": ref, "availability": "to be confirmed"}
        if name == "escalate_to_human":
            ref = f"ESC-{uuid4().hex[:8].upper()}"
            action = {"type": "escalated", "reference": ref, "details": args}
            self.actions.append(action)
            return {"escalated": True, "case_reference": ref, "summary": args["summary"]}
        return {"error": "Unknown tool."}
