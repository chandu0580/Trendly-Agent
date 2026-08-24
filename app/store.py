from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class OrderStore:
    """Read-only fixture repository. It never returns an order to another customer."""

    def __init__(self, source: Path | None = None) -> None:
        source = source or ROOT / "data" / "orders.json"
        raw = json.loads(source.read_text(encoding="utf-8"))
        self.orders = {o["order_id"].upper(): o for o in raw["orders"]}
        self.customers = {c["customer_id"]: c for c in raw["customers"]}

    def customer_exists(self, customer_id: str) -> bool:
        return customer_id in self.customers

    def get_order_for_customer(self, order_id: str, customer_id: str) -> dict | None:
        order = self.orders.get(order_id.upper())
        if not order or order["customer_id"] != customer_id:
            return None
        # Avoid leaking fixture-author annotations to the customer/model.
        return deepcopy({k: v for k, v in order.items() if not k.startswith("_")})
