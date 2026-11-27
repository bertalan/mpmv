"""ING.8: the pure stock and price planners (supplier rules: 404, stale, over 15 percent, sales)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from apps.ingest import engine as e

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
FRESH = NOW - timedelta(hours=1)
OLD = NOW - timedelta(days=5)


def stock(obs, qty=5):
    return e.plan_stock_actions([obs], {1: e.StockProduct(1, qty)}, now=NOW)[0]


def price(obs, net="100.00", **kw):
    return e.plan_price_actions([obs], {1: e.PriceProduct(1, Decimal(net))}, now=NOW, **kw)[0]


def sobs(**kw):
    base = {"ref": 9, "product_id": 1, "observed_at": FRESH, "stock_status": 1, "stock_quantity": 7}
    return e.StockObservation(**{**base, **kw})


def pobs(gross, **kw):
    base = {"ref": 9, "product_id": 1, "observed_at": FRESH, "price_gross": Decimal(gross)}
    return e.PriceObservation(**{**base, **kw})


def test_stock_exact_quantity_from_supplier():
    a = stock(sobs())
    assert (a.kind, a.quantity, a.ref, a.product_id) == (e.SET_STOCK, 7, 9, 1)


def test_stock_unchanged():
    assert stock(sobs(), qty=7).kind == e.UNCHANGED


def test_stock_404_sets_zero_and_never_unpublishes():
    a = stock(sobs(stock_status=None, stock_quantity=None, missing_status=404))
    assert (a.kind, a.quantity, a.reason) == (e.SET_STOCK, 0, "missing")


def test_stock_404_already_zero_is_unchanged():
    assert stock(sobs(missing_status=404), qty=0).kind == e.UNCHANGED


def test_stock_other_missing_status_is_skipped():
    a = stock(sobs(missing_status=503))
    assert (a.kind, a.reason) == (e.SKIP, "unreliable_status")


def test_stock_stale_is_flagged_never_applied():
    a = stock(sobs(observed_at=OLD))
    assert (a.kind, a.review_kind, a.quantity) == (e.REVIEW, "stale", None)


def test_stale_404_is_not_applied_either():
    assert stock(sobs(observed_at=OLD, missing_status=404)).kind == e.REVIEW


def test_stock_incoherent_fails_closed():
    for status, qty in ((1, 0), (0, 3), (None, None), (True, 1), (1, -2)):
        assert stock(sobs(stock_status=status, stock_quantity=qty)).kind == e.SKIP


def test_stock_unlinked_item_is_skipped():
    assert stock(sobs(product_id=None)).reason == "unlinked"
    assert stock(sobs(product_id=2)).reason == "unlinked"


def test_net_price_is_gross_over_122_half_up():
    assert e.net_from_gross(Decimal("122.00")) == Decimal("100.00")
    assert e.net_from_gross(Decimal("1.00")) == Decimal("0.82")
    assert e.net_from_gross(Decimal("0.61")) == Decimal("0.50")
    assert e.net_from_gross(Decimal("1.83")) == Decimal("1.50")  # 1.5 exactly
    assert e.net_from_gross(Decimal("1.22")) == Decimal("1.00")


def test_price_change_within_15_percent_applies():
    a = price(pobs("140.30"), net="100.00")  # 115.00 net: exactly 15 percent
    assert (a.kind, a.price_net, a.change_pct) == (e.SET_PRICE, Decimal("115.00"), Decimal("15.00"))


def test_price_change_over_15_percent_goes_to_review():
    a = price(pobs("140.42"), net="100.00")  # 115.10 net
    assert (a.kind, a.review_kind) == (e.REVIEW, "price_jump")
    assert a.price_net == Decimal("115.10")
    assert a.old_net == Decimal("100.00")
    assert a.change_pct == Decimal("15.10")


def test_price_drop_over_15_percent_also_goes_to_review():
    assert price(pobs("100.00"), net="100.00").review_kind == "price_jump"


def test_max_change_pct_is_a_parameter():
    assert price(pobs("134.20"), net="100.00", max_change_pct=Decimal("5")).kind == e.REVIEW


def test_price_unchanged():
    assert price(pobs("122.00"), net="100.00").kind == e.UNCHANGED


def test_first_price_applies_without_percentage():
    a = e.plan_price_actions([pobs("122.00")], {1: e.PriceProduct(1, None)}, now=NOW)[0]
    assert (a.kind, a.reason, a.change_pct) == (e.SET_PRICE, "first_price", None)


def test_sale_goes_to_review_and_holds_the_price():
    a = price(pobs("122.00", sale_price_gross=Decimal("100.00")), net="90.00")
    assert (a.kind, a.review_kind) == (e.REVIEW, "sale")


def test_price_stale_is_flagged_never_applied():
    a = price(pobs("122.00", observed_at=OLD), net="90.00")
    assert (a.kind, a.review_kind, a.price_net) == (e.REVIEW, "stale", None)


def test_invalid_price_is_skipped():
    for bad in (None, Decimal("0"), Decimal("-1"), Decimal("NaN")):
        assert price(e.PriceObservation(9, 1, FRESH, bad)).kind == e.SKIP


def test_unlinked_price_is_skipped():
    assert price(pobs("122.00", product_id=None)).kind == e.SKIP


def test_plan_keeps_order_one_action_per_observation():
    obs = [sobs(ref=3), sobs(ref=1, product_id=None), sobs(ref=2, observed_at=OLD)]
    plan = e.plan_stock_actions(obs, {1: e.StockProduct(1, 0)}, now=NOW)
    assert [a.ref for a in plan] == [3, 1, 2]
