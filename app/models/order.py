"""Order-domain models mirroring the supplied `orders.json` schema exactly."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

OrderStatus = Literal[
    "in_transit",
    "delivered",
    "partially_shipped",
    "delayed",
    "lost_in_transit",
    "cancelled",
]

ItemCategory = Literal["apparel", "accessories", "footwear", "innerwear", "jewellery"]

PaymentMethod = Literal["credit_card", "prepaid_card", "upi", "cash_on_delivery"]

# Policy 2.3. `socks` live under `innerwear` in the dataset; `beauty`, `fragrance`,
# `face_masks` and `gift_cards` do not appear in it but are listed so the rule
# stays faithful to the policy rather than to the fixture.
NON_RETURNABLE_CATEGORIES = frozenset(
    {"innerwear", "jewellery", "beauty", "fragrance", "face_masks", "gift_cards"}
)


class Item(BaseModel):
    """A line item. `sku` is the identifier the tool contracts call `item_id`."""

    sku: str
    name: str
    category: str
    size: str | None = None
    qty: int = 1
    price: float = 0
    final_sale: bool = False
    shipped: bool = True
    backorder_eta: str | None = None

    @property
    def is_non_returnable(self) -> bool:
        return self.category in NON_RETURNABLE_CATEGORIES


class Order(BaseModel):
    order_id: str
    customer_id: str
    status: str
    items: list[Item] = Field(default_factory=list)
    placed_at: str | None = None
    delivered_at: str | None = None
    expected_delivery: str | None = None
    cancelled_at: str | None = None
    carrier: str | None = None
    tracking_number: str | None = None
    payment_method: str | None = None
    shipping_city: str | None = None
    refund_status: str | None = None
    total: float = 0

    def item(self, item_id: str) -> Item | None:
        return next((i for i in self.items if i.sku.upper() == item_id.upper()), None)

    @property
    def backordered_items(self) -> list[Item]:
        return [i for i in self.items if not i.shipped]


class Customer(BaseModel):
    customer_id: str
    name: str | None = None
    email: str | None = None
    phone: str | None = None
