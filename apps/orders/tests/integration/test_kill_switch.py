"""ORD.10: the checkout and payments kill switches (ARC.8 flags) stop new orders, site-wide or for
one vendor, with a customer message in English and Italian, and never touch an order already
placed or paid."""

from collections.abc import Iterator

import pytest
from django.utils import translation

from apps.core import flags
from apps.core.audit import AuditContext
from apps.core.models import AuditLog, FeatureFlag
from apps.core.services import FLAG_CACHE, save_flag
from apps.orders.models import Order
from apps.orders.services import CheckoutClosed, ensure_checkout_open, place_order
from apps.orders.tests.integration.test_place_order import (
    CTX,
    NOW,
    line,
    place,
    preview,
    sellable,
    vendor_of,
)
from apps.vendors.models import Vendor, VendorFeatureFlag
from apps.vendors.services import save_vendor_flag

pytestmark = pytest.mark.django_db

STAFF = AuditContext(actor_label="staff:ops@example.com", source=AuditLog.Source.ADMIN, actor_id=7)


@pytest.fixture(autouse=True)
def _fresh_flag_cache() -> Iterator[None]:
    FLAG_CACHE.clear()
    yield
    FLAG_CACHE.clear()


def pull(name: str) -> None:
    row = FeatureFlag.objects.get(name=name)
    row.state = flags.OFF
    save_flag(row, ctx=STAFF)


def pull_for(name: str, vendor: Vendor) -> None:
    override = VendorFeatureFlag(
        vendor=vendor,
        flag=FeatureFlag.objects.get(name=name),
        enabled=False,
    )
    save_vendor_flag(override, ctx=STAFF)


@pytest.mark.parametrize("name", [flags.CHECKOUT, flags.PAYMENTS])
def test_a_pulled_site_switch_blocks_a_new_order_and_saves_nothing(name: str) -> None:
    product = sellable()
    pull(name)
    with pytest.raises(CheckoutClosed) as closed:
        place_order(preview(vendor_of(product, line(product))), ctx=CTX, now=NOW)
    assert closed.value.scope == "site"
    assert closed.value.flag == name
    assert Order.objects.count() == 0


def test_a_dry_run_is_blocked_too() -> None:
    product = sellable()
    pull(flags.CHECKOUT)
    with pytest.raises(CheckoutClosed):
        place_order(preview(vendor_of(product, line(product))), ctx=CTX, now=NOW, dry_run=True)


def test_an_order_placed_before_the_switch_is_left_alone() -> None:
    product = sellable()
    order = place(product)
    pull(flags.CHECKOUT)
    pull(flags.PAYMENTS)
    order.refresh_from_db()
    assert order.state == "awaiting_payment"
    assert Order.objects.filter(pk=order.pk).exists()


def test_a_paid_order_stays_paid_while_the_switches_are_off() -> None:
    product = sellable()
    order = place(product)
    Order.objects.filter(pk=order.pk).update(state="paid")
    pull(flags.CHECKOUT)
    pull(flags.PAYMENTS)
    order.refresh_from_db()
    assert order.state == "paid"


def test_a_vendor_switch_blocks_only_that_vendors_orders() -> None:
    closed_product = sellable()
    open_product = sellable()
    pull_for(flags.CHECKOUT, closed_product.vendor)
    with pytest.raises(CheckoutClosed) as closed:
        place_order(preview(vendor_of(closed_product, line(closed_product))), ctx=CTX, now=NOW)
    assert closed.value.scope == "vendor"
    assert closed.value.vendors == [closed_product.vendor.pk]
    order, _ = place_order(preview(vendor_of(open_product, line(open_product))), ctx=CTX, now=NOW)
    assert order is not None


def test_one_closed_vendor_refuses_a_mixed_cart_whole() -> None:
    closed_product = sellable()
    open_product = sellable()
    pull_for(flags.PAYMENTS, closed_product.vendor)
    wanted = preview(
        vendor_of(open_product, line(open_product)),
        vendor_of(closed_product, line(closed_product)),
    )
    with pytest.raises(CheckoutClosed):
        place_order(wanted, ctx=CTX, now=NOW)
    assert Order.objects.count() == 0


def test_the_platform_value_wins_over_a_vendor_that_is_still_on() -> None:
    product = sellable()
    pull(flags.CHECKOUT)
    with pytest.raises(CheckoutClosed) as closed:
        ensure_checkout_open([product.vendor])
    assert closed.value.scope == "site"


def test_flipping_the_switch_back_reopens_checkout() -> None:
    product = sellable()
    pull(flags.CHECKOUT)
    row = FeatureFlag.objects.get(name=flags.CHECKOUT)
    row.state = flags.DEFAULT
    save_flag(row, ctx=STAFF)
    order, _ = place_order(preview(vendor_of(product, line(product))), ctx=CTX, now=NOW)
    assert order is not None


def test_pulling_a_switch_is_audited() -> None:
    pull(flags.CHECKOUT)
    assert AuditLog.objects.filter(action="core.feature_flag.updated").exists()


def test_the_customer_message_is_translated_and_names_no_vendor() -> None:
    product = sellable()
    pull_for(flags.CHECKOUT, product.vendor)
    with pytest.raises(CheckoutClosed) as closed:
        ensure_checkout_open([product.vendor])
    with translation.override("en"):
        english = str(closed.value.customer_message)
    with translation.override("it"):
        italian = str(closed.value.customer_message)
    assert english != italian
    assert "tuo" in italian or "tua" in italian or "puoi" in italian
    assert str(product.vendor) not in english


def test_the_site_message_is_translated_too() -> None:
    product = sellable()
    pull(flags.PAYMENTS)
    with pytest.raises(CheckoutClosed) as closed:
        ensure_checkout_open([product.vendor])
    with translation.override("en"):
        english = str(closed.value.customer_message)
    with translation.override("it"):
        italian = str(closed.value.customer_message)
    assert english != italian
    assert "ordini" in italian
