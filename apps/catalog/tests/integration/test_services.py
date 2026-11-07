"""CAT.1: price changes go through services with dry-run, a ChangeSet and the audit log."""

import datetime as dt
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.catalog.models import Product, Variant
from apps.catalog.services import set_product_price, set_variant_price
from apps.catalog.tests.factories import ProductFactory, VariantFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:pricing", source="admin", ip="127.0.0.1")
JAN = dt.datetime(2027, 1, 1, tzinfo=dt.UTC)
FEB = dt.datetime(2027, 2, 1, tzinfo=dt.UTC)


def test_price_change_is_applied_and_audited() -> None:
    product = ProductFactory.create(sku="SKU-1", price_net=Decimal("10.00"))
    changes = set_product_price(
        product,
        price_net=Decimal("12.00"),
        sale_price_net=Decimal("9.50"),
        sale_from=JAN,
        sale_to=FEB,
        ctx=CTX,
    )
    assert changes.summary() == {"create": 0, "update": 1, "delete": 0}
    assert product.price_net == Decimal("12.00")
    product.refresh_from_db()
    assert product.price_net == Decimal("12.00")
    assert product.sale_price_net == Decimal("9.50")
    assert (product.sale_from, product.sale_to) == (JAN, FEB)

    entry = AuditLog.objects.get()
    assert entry.action == "catalog.product.price_changed"
    assert entry.target_model == "catalog.product"
    assert entry.target_key == str(product.public_id)
    before = {
        "sku": "SKU-1",
        "vendor": str(product.vendor.public_id),
        "price_net": "10.00",
        "sale_price_net": None,
        "sale_from": None,
        "sale_to": None,
    }
    assert entry.before == before
    assert entry.after == {
        **before,
        "price_net": "12.00",
        "sale_price_net": "9.50",
        "sale_from": JAN.isoformat(),
        "sale_to": FEB.isoformat(),
    }
    assert entry.actor_label == "staff:pricing"


def test_prices_given_as_strings_are_exact() -> None:
    product = ProductFactory.create()
    set_product_price(product, price_net="19.99", ctx=CTX)
    product.refresh_from_db()
    assert product.price_net == Decimal("19.99")


def test_dry_run_returns_the_change_without_applying_or_auditing() -> None:
    product = ProductFactory.create(price_net=Decimal("10.00"))
    changes = set_product_price(product, price_net=Decimal("11.00"), ctx=CTX, dry_run=True)
    assert changes.to_dict()["changes"] == [
        {
            "op": "update",
            "model": "catalog.product",
            "key": str(product.public_id),
            "fields": {"price_net": ["10.00", "11.00"]},
        }
    ]
    assert product.price_net == Decimal("10.00")
    product.refresh_from_db()
    assert product.price_net == Decimal("10.00")
    assert AuditLog.objects.count() == 0


def test_setting_the_same_price_changes_nothing() -> None:
    product = ProductFactory.create(price_net=Decimal("10.00"))
    changes = set_product_price(product, price_net="10.00", ctx=CTX)
    assert changes.is_empty
    assert AuditLog.objects.count() == 0


def test_omitting_the_sale_ends_it() -> None:
    product = ProductFactory.create(
        price_net=Decimal("10.00"), sale_price_net=Decimal("8.00"), sale_from=JAN, sale_to=FEB
    )
    set_product_price(product, price_net=Decimal("10.00"), ctx=CTX)
    product.refresh_from_db()
    assert (product.sale_price_net, product.sale_from, product.sale_to) == (None, None, None)


@pytest.mark.parametrize("dry_run", [False, True])
def test_invalid_prices_are_refused_and_nothing_changes(dry_run: bool) -> None:
    product = ProductFactory.create(price_net=Decimal("10.00"))
    with pytest.raises(ValidationError) as exc:
        set_product_price(
            product,
            price_net=Decimal("10.00"),
            sale_price_net=Decimal("10.00"),
            ctx=CTX,
            dry_run=dry_run,
        )
    assert "sale_price_net" in exc.value.message_dict
    product.refresh_from_db()
    assert product.sale_price_net is None
    assert AuditLog.objects.count() == 0


@pytest.mark.parametrize("price", ["-1.00", "10.001"])
def test_negative_or_sub_cent_prices_are_refused(price: str) -> None:
    product = ProductFactory.create()
    with pytest.raises(ValidationError) as exc:
        set_product_price(product, price_net=price, ctx=CTX)
    assert "price_net" in exc.value.message_dict
    assert AuditLog.objects.count() == 0


def test_float_prices_are_refused() -> None:
    with pytest.raises(TypeError):
        set_product_price(ProductFactory.create(), price_net=12.5, ctx=CTX)


def test_the_change_is_computed_against_the_stored_price_not_a_stale_instance() -> None:
    product = ProductFactory.create(price_net=Decimal("10.00"))
    stale = Product.objects.get(pk=product.pk)
    set_product_price(product, price_net=Decimal("12.00"), ctx=CTX)
    changes = set_product_price(stale, price_net=Decimal("15.00"), ctx=CTX)
    assert changes.changes[0].fields == {"price_net": (Decimal("12.00"), Decimal("15.00"))}
    assert AuditLog.objects.count() == 2


def test_variant_price_override_is_applied_and_audited() -> None:
    variant = VariantFactory.create(sku="VAR-1")
    changes = set_variant_price(variant, price_net_override=Decimal("14.00"), ctx=CTX)
    assert changes.summary()["update"] == 1
    assert variant.price_net_override == Decimal("14.00")
    variant.refresh_from_db()
    assert variant.price_net_override == Decimal("14.00")

    entry = AuditLog.objects.get()
    assert entry.action == "catalog.variant.price_changed"
    assert entry.target_model == "catalog.variant"
    assert entry.target_key == str(variant.public_id)
    before = {
        "sku": "VAR-1",
        "product": str(variant.product.public_id),
        "price_net_override": None,
    }
    assert entry.before == before
    assert entry.after == {**before, "price_net_override": "14.00"}


def test_a_variant_override_can_be_cleared() -> None:
    variant = VariantFactory.create(price_net_override=Decimal("14.00"))
    set_variant_price(variant, price_net_override=None, ctx=CTX)
    variant.refresh_from_db()
    assert variant.price_net_override is None
    after = AuditLog.objects.get().after or {}
    assert "price_net_override" in after
    assert after["price_net_override"] is None


def test_variant_dry_run_applies_nothing() -> None:
    variant = VariantFactory.create()
    changes = set_variant_price(variant, price_net_override=Decimal("14.00"), ctx=CTX, dry_run=True)
    assert not changes.is_empty
    assert Variant.objects.get(pk=variant.pk).price_net_override is None
    assert AuditLog.objects.count() == 0


def test_negative_variant_override_is_refused() -> None:
    with pytest.raises(ValidationError):
        set_variant_price(VariantFactory.create(), price_net_override="-1.00", ctx=CTX)
    assert AuditLog.objects.count() == 0
