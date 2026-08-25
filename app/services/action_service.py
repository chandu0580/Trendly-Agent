"""Creates and records state-changing actions, exactly once each.

Actions are simulated — nothing reaches a real returns, refund, or carrier
system — but they are recorded in a ledger that stands in for the returns system
of record. Policy 4.4 (one exchange per item) and duplicate-return checks are
evaluated against it.

**Idempotency.** An action's identity is `(customer_id, kind, order_id, item_id)`.
Submitting the same action twice returns the first reference rather than
creating a second return, whether the repeat comes from an impatient customer,
a model retrying after a timeout, or two concurrent requests. A retry that
returns a different reference is indistinguishable from a duplicate refund.

The lock matters: without it two concurrent submissions can both see "no
existing record" and both create one.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from uuid import uuid4

ActionKey = tuple[str, str, str, str]  # customer, kind, order, item


@dataclass(frozen=True)
class ActionRecord:
    reference: str
    kind: str
    customer_id: str
    order_id: str
    item_id: str
    detail: dict


@dataclass
class ActionLedger:
    """In-process stand-in for the returns system of record."""

    _actions: dict[ActionKey, ActionRecord] = field(default_factory=dict)
    _credits: dict[tuple[str, str], ActionRecord] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @staticmethod
    def _key(customer_id: str, kind: str, order_id: str, item_id: str) -> ActionKey:
        return (customer_id, kind, order_id.upper(), item_id.upper())

    # ------------------------------------------------------------- queries

    def find(self, customer_id: str, kind: str, order_id: str, item_id: str) -> ActionRecord | None:
        with self._lock:
            return self._actions.get(self._key(customer_id, kind, order_id, item_id))

    def return_count(self, order_id: str, item_id: str) -> int:
        """Returns raised for this item by any customer (there can only be one owner)."""
        with self._lock:
            return sum(
                1
                for (_c, kind, order, item) in self._actions
                if kind == "return" and order == order_id.upper() and item == item_id.upper()
            )

    def exchange_count(self, order_id: str, item_id: str) -> int:
        with self._lock:
            return sum(
                1
                for (_c, kind, order, item) in self._actions
                if kind == "exchange" and order == order_id.upper() and item == item_id.upper()
            )

    def credit_issued(self, order_id: str) -> bool:
        with self._lock:
            return any(order == order_id.upper() for (_c, order) in self._credits)

    def find_credit(self, customer_id: str, order_id: str) -> ActionRecord | None:
        with self._lock:
            return self._credits.get((customer_id, order_id.upper()))

    # ------------------------------------------------------------- mutation

    def submit(
        self,
        customer_id: str,
        kind: str,
        order_id: str,
        item_id: str,
        prefix: str,
        detail: dict,
    ) -> tuple[ActionRecord, bool]:
        """Record an action once. Returns (record, created).

        `created` is False when this exact action was already submitted, in which
        case the original record comes back untouched.
        """
        key = self._key(customer_id, kind, order_id, item_id)
        with self._lock:
            existing = self._actions.get(key)
            if existing is not None:
                return existing, False
            record = ActionRecord(
                reference=f"{prefix}-{uuid4().hex[:8].upper()}",
                kind=kind,
                customer_id=customer_id,
                order_id=order_id.upper(),
                item_id=item_id.upper(),
                detail=detail,
            )
            self._actions[key] = record
            return record, True

    def submit_credit(self, customer_id: str, order_id: str, detail: dict) -> tuple[ActionRecord, bool]:
        """Policy 1.5 credits are per order, not per item."""
        key = (customer_id, order_id.upper())
        with self._lock:
            existing = self._credits.get(key)
            if existing is not None:
                return existing, False
            record = ActionRecord(
                reference=f"CRD-{uuid4().hex[:8].upper()}",
                kind="credit",
                customer_id=customer_id,
                order_id=order_id.upper(),
                item_id="",
                detail=detail,
            )
            self._credits[key] = record
            return record, True

    @staticmethod
    def new_case_reference() -> str:
        """Escalations are not idempotent: two genuine handoffs are two tickets."""
        return f"ESC-{uuid4().hex[:8].upper()}"


_ledger = ActionLedger()


def get_action_ledger() -> ActionLedger:
    return _ledger


def reset_action_ledger() -> None:
    """Test hook: clears simulated actions between cases."""
    with _ledger._lock:
        _ledger._actions.clear()
        _ledger._credits.clear()
