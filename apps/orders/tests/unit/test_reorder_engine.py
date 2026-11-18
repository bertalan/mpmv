"""ORD.31: the pure rules of "order again"."""

from apps.orders import engine
from apps.orders.engine import ReorderWish, plan_reorder


def one(**kw: object) -> engine.ReorderOutcome:
    base: dict[str, object] = {"key": "k", "quantity": 2, "in_cart": 0, "limit": 10}
    return plan_reorder([ReorderWish(**{**base, **kw})])[0]  # type: ignore[arg-type]


def test_an_available_item_is_added_whole() -> None:
    out = one()
    assert (out.skipped, out.quantity, out.added, out.capped) == ("", 2, 2, False)


def test_a_retired_item_is_skipped_as_retired() -> None:
    assert one(limit=None).skipped == engine.REORDER_RETIRED


def test_no_stock_is_skipped_as_out_of_stock() -> None:
    assert one(limit=0).skipped == engine.REORDER_OUT_OF_STOCK


def test_an_undeliverable_item_is_skipped() -> None:
    assert one(undeliverable=True).skipped == engine.REORDER_NOT_DELIVERABLE


def test_an_item_in_the_cart_gets_the_quantities_added() -> None:
    out = one(in_cart=3)
    assert (out.quantity, out.added, out.capped) == (5, 2, False)


def test_the_total_is_capped_at_the_stock() -> None:
    out = one(in_cart=3, quantity=5, limit=6)
    assert (out.quantity, out.added, out.capped) == (6, 3, True)


def test_a_line_already_above_the_limit_is_not_reduced() -> None:
    out = one(in_cart=8, quantity=2, limit=5)
    assert (out.quantity, out.added, out.capped) == (8, 0, True)
