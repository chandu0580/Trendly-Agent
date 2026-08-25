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


class OrderRepository:
    def __init__(self, source=None) -> None:
        raw = json.loads((source or ORDERS_PATH).read_text(encoding="utf-8"))
        # `_`-prefixed keys are fixture-authoring annotations; strip them so they
        # can never reach the model or the customer.
        self._orders: dict[str, Order] = {
            o["order_id"].upper(): Order(**{k: v for k, v in o.items() if not k.startswith("_")})
            for o in raw["orders"]
        }
        self._customers: dict[str, Customer] = {
            c["customer_id"]: Customer(**c) for c in raw["customers"]
        }

    def customer_exists(self, customer_id: str) -> bool:
        return customer_id in self._customers

    def get_for_customer(self, order_id: str, customer_id: str) -> Order | None:
        """The only order accessor. Returns None for both 'no such order' and
        'not this customer's order' — the caller cannot tell them apart, so a
        reply cannot confirm that another customer's order exists."""
        order = self._orders.get(order_id.upper())
        if not order or order.customer_id != customer_id:
            return None
        return order.model_copy(deep=True)

    def orders_for_customer(self, customer_id: str) -> list[Order]:
        return [o.model_copy(deep=True) for o in self._orders.values() if o.customer_id == customer_id]

    @property
    def order_count(self) -> int:
        return len(self._orders)


@lru_cache(maxsize=1)
def get_order_repository() -> OrderRepository:
    return OrderRepository()
