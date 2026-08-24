from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY_TEXT = (ROOT / "data" / "trendly_policy.md").read_text(encoding="utf-8")
NON_RETURNABLE = {"innerwear", "jewellery", "beauty", "fragrance", "face_masks", "gift_cards"}


def today(value: str | None) -> date:
    return date.fromisoformat(value) if value else date.today()


def policy_answer(topic: str) -> dict:
    """Policy source and an intentionally bounded topical extract for the model."""
    needles = {
        "shipping": ("## 1. Shipping", "## 2. Returns"),
        "returns": ("## 2. Returns", "## 3. Refunds"),
        "refunds": ("## 3. Refunds", "## 4. Exchanges"),
        "exchanges": ("## 4. Exchanges", "## 5. Return pickup"),
        "pickup": ("## 5. Return pickup", "## 6. Damaged or wrong items"),
        "damaged": ("## 6. Damaged or wrong items", "## 7. What the assistant must not do"),
    }
    start, end = needles.get(topic.lower(), ("# Trendly", "---\n\n*Questions"))
    body = POLICY_TEXT.split(start, 1)[-1].split(end, 1)[0].strip()
    return {"source": "trendly_policy.md (effective 2026-01-01)", "topic": topic, "policy_excerpt": body}


def return_eligibility(order: dict, sku: str, as_of: date, reason: str = "change_of_mind") -> dict:
    item = next((x for x in order["items"] if x["sku"].upper() == sku.upper()), None)
    if not item:
        return {"eligible": False, "reason_code": "item_not_found", "reason": "That item is not on this order."}
    if order["status"] == "cancelled":
        return {"eligible": False, "reason_code": "cancelled", "reason": "Cancelled orders cannot have returns raised."}
    if not order.get("delivered_at"):
        return {"eligible": False, "reason_code": "not_delivered", "reason": "A return can be raised only after delivery."}
    delivered = date.fromisoformat(order["delivered_at"][:10])
    if as_of > delivered + timedelta(days=30):
        return {"eligible": False, "reason_code": "outside_window", "reason": "The 30-calendar-day return window has expired."}
    if item["category"] in NON_RETURNABLE and reason not in {"damaged", "defective", "wrong_item"}:
        return {"eligible": False, "reason_code": "non_returnable_category", "reason": f"{item['name']} is in a non-returnable category."}
    if item.get("final_sale"):
        return {"eligible": False, "reason_code": "final_sale_exchange_only", "reason": "Final-sale items are eligible for size exchange only, not a return/refund."}
    if reason in {"damaged", "defective", "wrong_item"} and as_of > delivered + timedelta(hours=48):
        return {"eligible": False, "reason_code": "damage_window_expired", "reason": "Damaged, defective, or wrong items must be reported within 48 hours of delivery with photos."}
    notes = ["Item must be unworn, unwashed, with tags and original packaging where provided."]
    if item["category"] == "footwear":
        notes.append("Return the original shoe box; without it, a ₹300 deduction applies.")
    if reason in {"damaged", "defective", "wrong_item"}:
        notes.append("Eligible for replacement or full refund including original ₹99 shipping, subject to photos.")
    return {"eligible": True, "reason_code": "eligible", "reason": "The item meets the recorded return eligibility rules.", "conditions": notes}


def exchange_eligibility(order: dict, sku: str, as_of: date, kind: str, prior_exchanges: int = 0) -> dict:
    item = next((x for x in order["items"] if x["sku"].upper() == sku.upper()), None)
    if not item:
        return {"eligible": False, "reason": "That item is not on this order."}
    if kind != "size":
        return {"eligible": False, "reason": "Trendly offers size exchanges only. Colour or style changes require a return and new order."}
    if not order.get("delivered_at") or as_of > date.fromisoformat(order["delivered_at"][:10]) + timedelta(days=30):
        return {"eligible": False, "reason": "The 30-calendar-day exchange window is not available."}
    if item["category"] in NON_RETURNABLE:
        return {"eligible": False, "reason": "This category cannot be exchanged."}
    if prior_exchanges >= 1:
        return {"eligible": False, "reason": "A second exchange needs human approval.", "needs_human": True}
    return {"eligible": True, "reason": "Eligible for one size exchange, subject to requested-size availability."}
