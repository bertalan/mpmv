"""ING.8: applying the stock and price plans (404, stale, over 15 percent, sales)."""

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest
from django.utils import timezone

from apps.catalog.models import Product, StockStatus
from apps.catalog.tests.factories import ProductFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.core.validity import VatRateUnavailable
from apps.ingest import sync
from apps.ingest.models import ImportRun, ReviewItem, SourceItem, SourceState
from apps.ingest.tests.factories import (
    ImportRunFactory,
    SourceItemFactory,
    SourceMissingFactory,
    SourceStateFactory,
    SupplierSourceFactory,
)

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="job:ingest", source="job")
MAX_AGE = dt.timedelta(hours=48)


def rate_for(product: Product, at: dt.datetime) -> Decimal:
    return Decimal("0.25")  # any rate: the service never owns one


def setup(dry_run: bool = False, **product: Any) -> tuple[ImportRun, SourceItem, Product]:
    source = SupplierSourceFactory.create(write_enabled=not dry_run)
    run = ImportRunFactory.create(source=source, dry_run=dry_run)
    prod = ProductFactory.create(vendor=source.vendor, **product)
    item = SourceItemFactory.create(source=source, product=prod)
    return run, item, prod


def state(item: SourceItem, **kw: Any) -> SourceState:
    state = {"kind": "simple", "sku": item.sku, **kw.pop("state", {})}
    return SourceStateFactory.create(item=item, state=state, **kw)


def stock(run: ImportRun) -> sync.SyncResult:
    return sync.sync_stock(run, ctx=CTX, now=timezone.now(), max_age=MAX_AGE)


def prices(run: ImportRun, **kw: Any) -> sync.SyncResult:
    return sync.sync_prices(
        run, ctx=CTX, now=timezone.now(), max_age=MAX_AGE, vat_rate_for=rate_for, **kw
    )


def test_stock_is_written_exactly_and_audited() -> None:
    run, item, prod = setup(stock_qty=0)
    state(item, stock_status=1, stock_quantity=7)
    result = stock(run)
    prod.refresh_from_db()
    assert (prod.stock_qty, prod.stock_status) == (7, StockStatus.IN_STOCK)
    assert result.applied == 1
    assert AuditLog.objects.filter(action="catalog.sku_rows_updated").exists()
    assert AuditLog.objects.filter(action=sync.SYNC_RUN).count() == 1


def test_a_404_sets_stock_to_zero_and_keeps_the_product_status() -> None:
    run, item, prod = setup(stock_qty=4, status=Product.Status.PUBLISHED)
    SourceMissingFactory.create(item=item, http_status=404)
    stock(run)
    prod.refresh_from_db()
    assert (prod.stock_qty, prod.stock_status) == (0, StockStatus.OUT_OF_STOCK)
    assert prod.status == Product.Status.PUBLISHED


def test_stale_stock_is_flagged_not_applied() -> None:
    run, item, prod = setup(stock_qty=4)
    state(item, stock_status=1, stock_quantity=9, observed_at=timezone.now() - dt.timedelta(days=5))
    result = stock(run)
    prod.refresh_from_db()
    assert prod.stock_qty == 4
    review = ReviewItem.objects.get()
    assert (review.kind, review.blocking, review.product_id) == ("stale", False, prod.pk)
    assert result.reviews == 1


def test_the_same_open_review_is_not_opened_twice() -> None:
    run, item, _ = setup()
    state(item, stock_status=1, stock_quantity=9, observed_at=timezone.now() - dt.timedelta(days=5))
    stock(run)
    stock(run)
    assert ReviewItem.objects.count() == 1


def test_a_dry_run_writes_nothing_but_reports_the_changes() -> None:
    run, item, prod = setup(dry_run=True, stock_qty=1)
    state(item, stock_status=1, stock_quantity=8)
    result = stock(run)
    prod.refresh_from_db()
    assert prod.stock_qty == 1
    assert not result.changes.is_empty
    assert result.applied == 0
    assert not AuditLog.objects.filter(action=sync.SYNC_RUN).exists()


def test_the_dry_run_flag_of_the_call_is_honoured_on_a_write_run() -> None:
    run, item, prod = setup(stock_qty=1)
    state(item, stock_status=1, stock_quantity=8)
    sync.sync_stock(run, ctx=CTX, now=timezone.now(), max_age=MAX_AGE, dry_run=True)
    prod.refresh_from_db()
    assert prod.stock_qty == 1


def test_a_price_within_the_limit_is_applied_as_net() -> None:
    run, item, prod = setup(price_net=Decimal("100.00"))
    state(item, state={"price": "137.50"})  # 110.00 net at 25 percent
    result = prices(run)
    prod.refresh_from_db()
    assert prod.price_net == Decimal("110.00")
    assert result.applied == 1
    assert AuditLog.objects.filter(action="catalog.product.price_changed").exists()


def test_a_price_jump_goes_to_review_and_is_not_applied() -> None:
    run, item, prod = setup(price_net=Decimal("100.00"))
    state(item, state={"price": "162.50"})  # 130.00 net
    result = prices(run)
    prod.refresh_from_db()
    assert prod.price_net == Decimal("100.00")
    review = ReviewItem.objects.get()
    assert review.kind == "price_jump"
    assert result.reviews == 1
    assert review.payload["new_net"] == "130.00"
    assert review.payload["old_net"] == "100.00"


def test_a_sale_goes_to_review_and_keeps_the_price() -> None:
    run, item, prod = setup(price_net=Decimal("100.00"))
    state(item, state={"price": "125.00", "sale_price": "100.00"})
    prices(run)
    prod.refresh_from_db()
    assert prod.price_net == Decimal("100.00")
    assert ReviewItem.objects.get().kind == "sale"


def test_stale_prices_are_flagged_never_applied() -> None:
    run, item, prod = setup(price_net=Decimal("100.00"))
    state(item, state={"price": "130.00"}, observed_at=timezone.now() - dt.timedelta(days=9))
    prices(run)
    prod.refresh_from_db()
    assert prod.price_net == Decimal("100.00")
    assert ReviewItem.objects.get().kind == "stale"


def test_a_price_change_keeps_the_products_own_sale() -> None:
    run, item, prod = setup(price_net=Decimal("100.00"), sale_price_net=Decimal("80.00"))
    state(item, state={"price": "137.50"})
    prices(run)
    prod.refresh_from_db()
    assert (prod.price_net, prod.sale_price_net) == (Decimal("110.00"), Decimal("80.00"))


def test_a_product_without_a_rate_is_an_error_not_a_guess() -> None:
    run, item, prod = setup(price_net=Decimal("100.00"))
    state(item, state={"price": "137.50"})

    def no_rate(product: Product, at: dt.datetime) -> Decimal:
        raise VatRateUnavailable("no tax rate")

    result = sync.sync_prices(
        run, ctx=CTX, now=timezone.now(), max_age=MAX_AGE, vat_rate_for=no_rate
    )
    prod.refresh_from_db()
    assert prod.price_net == Decimal("100.00")
    assert len(result.errors) == 1


def test_price_dry_run_writes_nothing() -> None:
    run, item, prod = setup(dry_run=True, price_net=Decimal("100.00"))
    state(item, state={"price": "137.50"})
    result = prices(run)
    prod.refresh_from_db()
    assert prod.price_net == Decimal("100.00")
    assert not result.changes.is_empty


def test_a_vat_exempt_vendor_rate_of_zero_makes_net_equal_gross() -> None:
    """Pins the current behaviour (lane-a question open): rate 0 means no VAT is taken out."""
    run, item, prod = setup(price_net=Decimal("100.00"))
    state(item, state={"price": "110.00"})
    sync.sync_prices(
        run, ctx=CTX, now=timezone.now(), max_age=MAX_AGE, vat_rate_for=lambda p, at: Decimal("0")
    )
    prod.refresh_from_db()
    assert prod.price_net == Decimal("110.00")


def test_the_rate_is_looked_up_once_per_category_vendor_and_day() -> None:
    run, item, _ = setup(price_net=Decimal("100.00"))
    state(item, state={"price": "100.00"})
    other = ProductFactory.create(vendor=run.source.vendor, price_net=Decimal("100.00"))
    state(SourceItemFactory.create(source=run.source, product=other), state={"price": "100.00"})
    calls: list[str] = []

    def counting(product: Product, at: dt.datetime) -> Decimal:
        calls.append(product.sku)
        return Decimal("0.25")

    sync.sync_prices(run, ctx=CTX, now=timezone.now(), max_age=MAX_AGE, vat_rate_for=counting)
    assert len(calls) == 1


def test_a_rejected_review_is_not_reopened_for_the_same_observation() -> None:
    run, item, _ = setup(price_net=Decimal("100.00"))
    row = state(item, state={"price": "162.50"})
    prices(run)
    ReviewItem.objects.update(decision="rejected", resolved_at=timezone.now())
    prices(run)
    assert ReviewItem.objects.count() == 1
    row.state = {**row.state, "price": "187.50"}  # the supplier moved again: a new question
    row.save()
    prices(run)
    assert ReviewItem.objects.count() == 2


def test_a_plain_404_opens_no_review() -> None:
    run, item, _ = setup(stock_qty=3)
    SourceMissingFactory.create(item=item, http_status=404)
    result = stock(run)
    assert result.reviews == 0
    assert not ReviewItem.objects.exists()


def test_a_stale_404_is_flagged_and_stock_stays() -> None:
    run, item, prod = setup(stock_qty=3)
    SourceMissingFactory.create(
        item=item, http_status=404, last_seen_at=timezone.now() - dt.timedelta(days=5)
    )
    stock(run)
    prod.refresh_from_db()
    assert prod.stock_qty == 3
    assert ReviewItem.objects.get().kind == "stale"


def test_two_rates_are_planned_together_and_keep_item_order() -> None:
    run, item, prod = setup(price_net=Decimal("100.00"))
    state(item, state={"price": "110.00"})
    other = ProductFactory.create(
        vendor=run.source.vendor, price_net=Decimal("100.00"), fiscal_category="reduced"
    )
    state(SourceItemFactory.create(source=run.source, product=other), state={"price": "110.00"})
    result = sync.sync_prices(
        run,
        ctx=CTX,
        now=timezone.now(),
        max_age=MAX_AGE,
        vat_rate_for=lambda p, at: (
            Decimal("0.10") if p.fiscal_category == "reduced" else Decimal("0")
        ),
    )
    assert [a.ref for a in result.actions] == sorted(a.ref for a in result.actions)
    prod.refresh_from_db()
    other.refresh_from_db()
    assert (prod.price_net, other.price_net) == (Decimal("110.00"), Decimal("100.00"))


def test_a_newer_supplier_value_refreshes_the_open_review() -> None:
    run, item, _ = setup(price_net=Decimal("100.00"))
    row = state(item, state={"price": "162.50"})  # 130.00 net
    prices(run)
    row.state = {**row.state, "price": "187.50"}  # 150.00 net
    row.save()
    result = prices(run)
    review = ReviewItem.objects.get()
    assert result.reviews == 0
    assert review.payload["new_net"] == "150.00"
    assert review.payload["change_pct"] == "50.00"
    assert AuditLog.objects.filter(action=sync.REVIEW_REFRESHED).count() == 1


def test_the_database_allows_one_open_review_per_product_and_kind() -> None:
    from django.db import IntegrityError, transaction

    run, item, prod = setup()
    ReviewItem.objects.create(run=run, item=item, product=prod, kind="sale", payload={})
    with pytest.raises(IntegrityError), transaction.atomic():
        ReviewItem.objects.create(run=run, item=item, product=prod, kind="sale", payload={})
    ReviewItem.objects.update(decision="rejected", resolved_at=timezone.now())
    ReviewItem.objects.create(run=run, item=item, product=prod, kind="sale", payload={})


def test_losing_the_race_to_open_a_review_is_read_as_already_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, item, prod = setup()
    ReviewItem.objects.create(run=run, item=item, product=prod, kind="stale", payload={})
    # A concurrent run committed its open item after our lookup: hide it from the lookup.
    monkeypatch.setattr(
        "apps.ingest.sync.ReviewItem.objects.filter",
        lambda *a, **k: ReviewItem.objects.none(),
    )
    state(item, stock_status=1, stock_quantity=9, observed_at=timezone.now() - dt.timedelta(days=5))
    result = stock(run)
    assert result.reviews == 0


def test_the_dry_run_counts_the_would_be_reviews() -> None:
    run, item, _ = setup(dry_run=True, price_net=Decimal("100.00"))
    state(item, state={"price": "162.50"})
    result = prices(run)
    assert result.reviews == 1
    assert not ReviewItem.objects.exists()


def test_a_programming_error_in_the_resolver_is_not_hidden_as_an_error_row() -> None:
    run, item, _ = setup(price_net=Decimal("100.00"))
    state(item, state={"price": "137.50"})

    def broken(product: Product, at: dt.datetime) -> Decimal:
        raise TypeError("bug")

    with pytest.raises(TypeError):
        sync.sync_prices(run, ctx=CTX, now=timezone.now(), max_age=MAX_AGE, vat_rate_for=broken)
