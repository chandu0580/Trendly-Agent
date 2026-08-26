"""Read-only access to the supplied order dataset.

`orders.json` is loaded as provided and never mutated. The only way to reach an
order is through a customer-scoped call, so ownership is a property of the data
layer rather than something a caller can forget to check.
"""

from __future__ import annotations

import json
from functools import lru_cache

from ..config import ORDERS_PATH
from ..models.order import Customer, Order


def _key(value: str | None) -> str:
    """Normalise an identifier for lookup.

    Customers type `c-100` and ` C-100 ` as readily as `C-100`. Order ids were
    already uppercased on the way in while customer ids were not, so the same
    person was recognised or rejected depending on their shift key. Case and
    surrounding space are not part of an identity.
    """
    return (value or "").strip().upper()


class OrderRepository:
    def __init__(self, source=None) -> None:
        raw = json.loads((source or ORDERS_PATH).read_text(encoding="utf-8"))
        # `_`-prefixed keys are fixture-authoring annotations; strip them so they
        # can never reach the model or the customer.
        self._orders: dict[str, Order] = {
            _key(o["order_id"]): Order(**{k: v for k, v in o.items() if not k.startswith("_")})
            for o in raw["orders"]
        }
        self._customers: dict[str, Customer] = {
            _key(c["customer_id"]): Customer(**c) for c in raw["customers"]
        }

    def customer_exists(self, customer_id: str) -> bool:
        return _key(customer_id) in self._customers

    def get_for_customer(self, order_id: str, customer_id: str) -> Order | None:
        """The only order accessor. Returns None for both 'no such order' and
        'not this customer's order' — the caller cannot tell them apart, so a
        reply cannot confirm that another customer's order exists."""
        order = self._orders.get(_key(order_id))
        if not order or _key(order.customer_id) != _key(customer_id):
            return None
        return order.model_copy(deep=True)

    def orders_for_customer(self, customer_id: str) -> list[Order]:
        wanted = _key(customer_id)
        return [o.model_copy(deep=True) for o in self._orders.values() if _key(o.customer_id) == wanted]

    @property
    def order_count(self) -> int:
        return len(self._orders)


@lru_cache(maxsize=1)
def get_order_repository() -> OrderRepository:
    return OrderRepository()
