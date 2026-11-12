"""OP-28 (Omnibus): which product reviews count as verified (pure, no database)."""

import pytest

from apps.orders import engine

BUYER, OTHER, PRODUCT, OTHER_PRODUCT = 1, 2, 10, 11


def purchase(
    line: int = 100,
    *,
    customer: int | None = BUYER,
    product: int | None = PRODUCT,
    state: str = "paid",
) -> engine.PurchaseFact:
    return engine.PurchaseFact(
        line_id=line, customer_id=customer, product_id=product, order_state=state
    )


@pytest.mark.parametrize("state", ["paid", "in_fulfillment", "fulfilled", "closed"])
def test_a_paid_order_of_the_same_customer_and_product_verifies(state: str) -> None:
    assert engine.verifying_line(BUYER, PRODUCT, [purchase(state=state)]) == 100


@pytest.mark.parametrize(
    "state",
    ["checkout", "quote_flow", "awaiting_payment", "cancelled", "refunded", "partially_refunded"],
)
def test_an_order_that_is_unpaid_or_undone_does_not_verify(state: str) -> None:
    assert engine.verifying_line(BUYER, PRODUCT, [purchase(state=state)]) is None


def test_another_customer_or_another_product_does_not_verify() -> None:
    facts = [purchase(1, customer=OTHER), purchase(2, product=OTHER_PRODUCT)]
    assert engine.verifying_line(BUYER, PRODUCT, facts) is None


def test_a_guest_order_or_a_review_without_a_customer_never_verifies() -> None:
    assert engine.verifying_line(BUYER, PRODUCT, [purchase(customer=None)]) is None
    assert engine.verifying_line(None, PRODUCT, [purchase(customer=None)]) is None


def test_a_deleted_product_never_verifies() -> None:
    assert engine.verifying_line(BUYER, None, [purchase(product=None)]) is None


def test_the_earliest_matching_line_is_chosen() -> None:
    facts = [purchase(7), purchase(3), purchase(5, state="cancelled")]
    assert engine.verifying_line(BUYER, PRODUCT, facts) == 3


def test_the_badge_follows_the_verification_only() -> None:
    assert engine.review_badge(verified=True) == "verified"
    assert engine.review_badge(verified=False) == "not_verified"
