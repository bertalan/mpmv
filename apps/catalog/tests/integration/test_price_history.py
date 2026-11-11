"""CMP.6 (1/2): the append-only price history, recorded by the price services."""

import datetime as dt
from decimal import Decimal
from unittest import mock

import pytest
from django.db import IntegrityError, connection, transaction

from apps.catalog import engine, services
from apps.catalog.models import PriceHistory, Product
from apps.catalog.tests.factories import ProductFactory, VariantFactory
from apps.core.audit import AuditContext
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:pricing", source="admin", ip="127.0.0.1")


def at(n: int) -> dt.datetime:
    return dt.datetime(2027, 1, 1, 12, tzinfo=dt.UTC) + dt.timedelta(days=n - 1)


def set_price(product, n: int, price: str, sale: str | None = None, **kw) -> None:  # type: ignore[no-untyped-def]
    with mock.patch("apps.catalog.services.timezone.now", return_value=at(n)):
        services.set_product_price(
            product,
            price_net=Decimal(price),
            sale_price_net=None if sale is None else Decimal(sale),
            ctx=CTX,
            **kw,
        )


def test_every_product_price_change_appends_a_row() -> None:
    product = ProductFactory.create(price_net=Decimal("10.00"))
    set_price(product, 1, "12.00")
    set_price(product, 2, "12.00", "9.00")
    rows = list(PriceHistory.objects.filter(product=product, variant=None).order_by("recorded_at"))
    assert [(r.price_net, r.sale_price_net, r.currency) for r in rows] == [
        (Decimal("12.00"), None, "EUR"),
        (Decimal("12.00"), Decimal("9.00"), "EUR"),
    ]
    assert rows[0].recorded_at == at(1)


def test_dry_run_records_nothing() -> None:
    product = ProductFactory.create(price_net=Decimal("10.00"))
    services.set_product_price(product, price_net=Decimal("11.00"), ctx=CTX, dry_run=True)
    assert not PriceHistory.objects.exists()


def test_refused_price_records_nothing() -> None:
    product = ProductFactory.create(price_net=Decimal("10.00"))
    with pytest.raises(Exception):  # noqa: B017, PT011
        services.set_product_price(product, price_net=Decimal("-1.00"), ctx=CTX)
    assert not PriceHistory.objects.exists()


def test_a_new_product_with_a_price_starts_its_history() -> None:
    product = ProductFactory.build(price_net=Decimal("10.00"), vendor=VendorFactory.create())
    services.save_product(product, ctx=CTX)
    row = PriceHistory.objects.get(product=product)
    assert (row.price_net, row.variant_id) == (Decimal("10.00"), None)


def test_variant_override_changes_are_recorded_on_the_variant() -> None:
    product = ProductFactory.create(price_net=Decimal("10.00"), kind=Product.Kind.VARIABLE)
    variant = VariantFactory.create(product=product)
    services.set_variant_price(variant, price_net_override=Decimal("15.00"), ctx=CTX)
    row = PriceHistory.objects.get(variant=variant)
    assert (row.product_id, row.price_net) == (product.pk, Decimal("15.00"))


def _raw_update() -> None:
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("UPDATE catalog_pricehistory SET price_net = 1")


def test_rows_cannot_be_updated_or_changed() -> None:
    product = ProductFactory.create(price_net=Decimal("10.00"))
    set_price(product, 1, "12.00")
    row = PriceHistory.objects.get()
    row.price_net = Decimal("1.00")
    with pytest.raises(PermissionError):
        row.save()
    with pytest.raises(Exception, match="append-only"):
        _raw_update()
    assert PriceHistory.objects.get().price_net == Decimal("12.00")


def test_prices_must_be_positive_and_currency_is_three_letters() -> None:
    product = ProductFactory.create()
    with pytest.raises(IntegrityError), transaction.atomic():
        PriceHistory.objects.bulk_create(
            [PriceHistory(product=product, price_net=Decimal("-1.00"), currency="EUR")]
        )


def test_reference_price_for_a_product_on_sale() -> None:
    product = ProductFactory.create(price_net=Decimal("90.00"))
    set_price(product, 1, "100.00")
    set_price(product, 40, "100.00", "70.00")
    found = services.reference_price(product, at=at(41))
    assert found is not None
    assert (found.price_net, found.current_net) == (Decimal("100.00"), Decimal("70.00"))


def test_variant_history_follows_the_product_price_when_not_overridden() -> None:
    product = ProductFactory.create(price_net=Decimal("90.00"), kind=Product.Kind.VARIABLE)
    variant = VariantFactory.create(product=product)
    set_price(product, 1, "100.00")
    set_price(product, 40, "100.00", "70.00")
    found = services.reference_price(product, variant=variant, at=at(41))
    assert found is not None
    assert found.price_net == Decimal("100.00")


def test_an_unchanged_price_appends_nothing() -> None:
    product = ProductFactory.create(price_net=Decimal("10.00"))
    set_price(product, 1, "10.00")
    assert not PriceHistory.objects.exists()


def test_a_cleared_variant_override_is_a_point_even_without_a_product_row() -> None:
    product = ProductFactory.create(price_net=Decimal("10.00"), kind=Product.Kind.VARIABLE)
    variant = VariantFactory.create(product=product)
    for n, value in ((1, Decimal("15.00")), (2, None)):
        with mock.patch("apps.catalog.services.timezone.now", return_value=at(n)):
            services.set_variant_price(variant, price_net_override=value, ctx=CTX)
    points = services.price_points(product, variant)
    assert [p.price_net for p in points] == [Decimal("15.00"), None]


def test_the_sale_rule_for_overridden_variants_is_one_named_switch() -> None:
    # Owner decision 2026-11-11: a variant override wins over the product sale.
    assert engine.SALE_APPLIES_TO_OVERRIDDEN_VARIANT is False


def test_a_direct_delete_is_refused() -> None:
    product = ProductFactory.create(price_net=Decimal("10.00"))
    set_price(product, 1, "12.00")
    with pytest.raises(Exception, match="append-only"), transaction.atomic():
        PriceHistory.objects.all().delete()
    assert PriceHistory.objects.count() == 1


def test_rows_go_with_their_product_through_delete_product() -> None:
    product = ProductFactory.create(price_net=Decimal("10.00"))
    set_price(product, 1, "12.00")
    services.delete_product(product, ctx=CTX)
    assert not PriceHistory.objects.exists()


def test_rows_go_with_their_variant_through_delete_variant() -> None:
    product = ProductFactory.create(price_net=Decimal("10.00"), kind=Product.Kind.VARIABLE)
    variant = VariantFactory.create(product=product)
    services.set_variant_price(variant, price_net_override=Decimal("15.00"), ctx=CTX)
    services.delete_variant(variant, ctx=CTX)
    assert not PriceHistory.objects.filter(variant__isnull=False).exists()
