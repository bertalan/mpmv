"""CMP.6 (1/2): the append-only price history, recorded by the price services."""

import datetime as dt
from decimal import Decimal
from unittest import mock

import pytest
from django.db import IntegrityError, connection, transaction

from apps.catalog import services
from apps.catalog.models import PriceHistory
from apps.catalog.tests.factories import ProductFactory, VariantFactory
from apps.core.audit import AuditContext

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
    product = ProductFactory.build(price_net=Decimal("10.00"))
    services.save_product(product, ctx=CTX)
    row = PriceHistory.objects.get(product=product)
    assert (row.price_net, row.variant_id) == (Decimal("10.00"), None)


def test_variant_override_changes_are_recorded_on_the_variant() -> None:
    product = ProductFactory.create(price_net=Decimal("10.00"))
    variant = VariantFactory.create(product=product)
    services.set_variant_price(variant, price_net_override=Decimal("15.00"), ctx=CTX)
    row = PriceHistory.objects.get(variant=variant)
    assert (row.product_id, row.price_net) == (product.pk, Decimal("15.00"))


def test_rows_cannot_be_updated_or_changed() -> None:
    product = ProductFactory.create(price_net=Decimal("10.00"))
    set_price(product, 1, "12.00")
    row = PriceHistory.objects.get()
    row.price_net = Decimal("1.00")
    with pytest.raises(PermissionError):
        row.save()
    with (
        transaction.atomic(),
        pytest.raises(Exception, match="append-only"),
        connection.cursor() as cursor,
    ):
        cursor.execute("UPDATE catalog_pricehistory SET price_net = 1")
    assert PriceHistory.objects.get().price_net == Decimal("12.00")


def test_prices_must_be_positive_and_currency_is_three_letters() -> None:
    product = ProductFactory.create()
    with transaction.atomic(), pytest.raises(IntegrityError):
        PriceHistory.objects.bulk_create(
            [PriceHistory(product=product, price_net=Decimal("-1.00"), currency="EUR")]
        )


def test_reference_price_for_a_product_on_sale() -> None:
    product = ProductFactory.create(price_net=Decimal("100.00"))
    set_price(product, 1, "100.00")
    set_price(product, 40, "100.00", "70.00")
    found = services.reference_price(product, at=at(41))
    assert found is not None
    assert (found.price_net, found.current_net) == (Decimal("100.00"), Decimal("70.00"))


def test_variant_history_follows_the_product_price_when_not_overridden() -> None:
    product = ProductFactory.create(price_net=Decimal("100.00"))
    variant = VariantFactory.create(product=product)
    set_price(product, 1, "100.00")
    set_price(product, 40, "100.00", "70.00")
    found = services.reference_price(product, variant=variant, at=at(41))
    assert found is not None
    assert found.price_net == Decimal("100.00")
