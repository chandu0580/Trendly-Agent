"""The deterministic customer+order verification function.

This is the whole ownership decision, isolated from the agent, the tools, and the
HTTP layer — which is the point: it can be read and tested without reasoning
about any of them.
"""

from __future__ import annotations

import json

import pytest

from app.services.authorization import (
    CUSTOMER_NOT_RECOGNISED,
    ORDER_NOT_ACCESSIBLE,
    VERIFICATION_REQUIRED,
    verify_customer_order_access,
)
from tests.conftest import OWNERSHIP, sensitive_values

pytestmark = pytest.mark.unit

CUSTOMERS = sorted(OWNERSHIP)
ALL_ORDERS = sorted(o for orders in OWNERSHIP.values() for o in orders)
COMBINATIONS = [(c, o) for c in CUSTOMERS for o in ALL_ORDERS]
ALLOWED = {(c, o) for c, orders in OWNERSHIP.items() for o in orders}


def test_the_matrix_covers_every_pairing():
    assert len(COMBINATIONS) == 40
    assert len(ALLOWED) == 10
    assert len(COMBINATIONS) - len(ALLOWED) == 30


@pytest.mark.parametrize(
    "customer_id,order_id", COMBINATIONS, ids=[f"{c}-{o}" for c, o in COMBINATIONS]
)
def test_ownership_matrix(customer_id, order_id):
    result = verify_customer_order_access(customer_id, order_id)

    if (customer_id, order_id) in ALLOWED:
        assert result["authorized"] is True
        assert result["customer_id"] == customer_id
        assert result["order_id"] == order_id
    else:
        assert result["authorized"] is False
        assert result["reason_code"] == ORDER_NOT_ACCESSIBLE
        # The denial carries the ids that were asked about and nothing else.
        blob = json.dumps(result).lower()
        for value in sensitive_values(order_id):
            assert value.lower() not in blob, f"denial leaked {value!r}"


def test_ownership_is_read_from_the_dataset_not_inferred():
    """The relationship checked is order.customer_id == customer_id, nothing else."""
    from app.services.order_repository import get_order_repository

    repo = get_order_repository()
    for customer_id, orders in OWNERSHIP.items():
        for order_id in orders:
            assert repo.get_for_customer(order_id, customer_id).customer_id == customer_id


# --------------------------------------------------------------- missing input


@pytest.mark.parametrize(
    "customer_id,order_id,missing",
    [
        (None, "TR-4524", ["customer_id"]),
        ("C-100", None, ["order_id"]),
        (None, None, ["customer_id", "order_id"]),
        ("", "  ", ["customer_id", "order_id"]),
    ],
)
def test_missing_identifiers_are_reported_individually(customer_id, order_id, missing):
    """So the agent asks only for what it actually needs."""
    result = verify_customer_order_access(customer_id, order_id)
    assert result["authorized"] is False
    assert result["reason_code"] == VERIFICATION_REQUIRED
    assert result["missing"] == missing


# ------------------------------------------------------------- invalid input


@pytest.mark.parametrize("bad", ["C-999", "C-000", "c-1000", "admin", "C-10", "'; DROP TABLE --"])
def test_an_unrecognised_customer_is_reported_as_such(bad):
    """Safe to say — it discloses nothing about any order."""
    result = verify_customer_order_access(bad, "TR-4524")
    assert result["authorized"] is False
    assert result["reason_code"] == CUSTOMER_NOT_RECOGNISED


@pytest.mark.parametrize("bad", ["TR-9999", "TR-0000", "XX-1234", "TR-452"])
def test_an_unknown_order_is_indistinguishable_from_someone_elses(bad):
    """Both are ORDER_NOT_ACCESSIBLE: telling them apart would confirm existence."""
    unknown = verify_customer_order_access("C-100", bad)
    other = verify_customer_order_access("C-100", "TR-4522")
    assert unknown == other == {"authorized": False, "reason_code": ORDER_NOT_ACCESSIBLE}


def test_identifiers_are_normalised_but_not_guessed():
    assert verify_customer_order_access("C-100", "tr-4524")["authorized"] is True
    assert verify_customer_order_access("  C-100  ", " TR-4524 ")["authorized"] is True
    # Normalising case is not the same as completing a partial id.
    assert verify_customer_order_access("C-100", "4524")["authorized"] is False


def test_no_order_object_is_ever_returned():
    """Callers that are authorised fetch the order through the scoped repository."""
    for customer_id, order_id in COMBINATIONS:
        result = verify_customer_order_access(customer_id, order_id)
        assert set(result) <= {"authorized", "reason_code", "customer_id", "order_id", "missing"}
