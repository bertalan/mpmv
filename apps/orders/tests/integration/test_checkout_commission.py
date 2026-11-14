"""ORD.5 / VEN.2: the checkout takes the commission rule in force, through the real seam.

`state.commission_snapshots` asks `apps.vendors.services.commission_rule_for` for each vendor
of the cart. With a platform default rule the review offers placing the order; a vendor's own
rule wins over the default; with no rule at all ordering stays closed (never a silent 0%). No
monkeypatching here: the rules are real rows.
"""

from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from apps.core.tests.factories import utc
from apps.orders.models import Order, VendorOrder
from apps.orders.services import COMMISSION_KEYS, COMMISSION_OPTIONAL_KEYS
from apps.orders.tests.integration.test_checkout_views import (
    add,
    sign_off_details,
    tax_setup,  # noqa: F401  (autouse fixture: operator and tax rates)
    vendor,
    with_cart,
)
from apps.orders.ui import state
from apps.vendors.models import CommissionRule
from apps.vendors.tests.factories import CommissionRuleFactory

pytestmark = pytest.mark.django_db

D = Decimal


def default_rule() -> CommissionRule:
    return CommissionRuleFactory.create(valid_from=utc(2026, 1, 1), valid_to=None)


def test_a_platform_default_rule_opens_ordering(client: Client) -> None:
    rule = default_rule()
    cart = with_cart(client)
    seller = vendor("Alpha Shop")
    add(cart, seller, "100.00")
    sign_off_details(client)
    html = client.get(reverse("orders:review")).content.decode()
    assert "Ordering is not open yet" not in html
    assert "Place order" in html
    response = client.post(reverse("orders:review"))
    assert response.status_code == 302
    placed = VendorOrder.objects.get(order=Order.objects.get(), vendor=seller)
    assert placed.commission_snapshot == rule.snapshot()


def test_a_vendor_rule_wins_over_the_platform_default(client: Client) -> None:
    default_rule()
    seller = vendor("Beta Shop")
    own = CommissionRuleFactory.create(
        vendor=seller, percent=D("12.50"), valid_from=utc(2026, 1, 1), valid_to=None
    )
    cart = with_cart(client)
    add(cart, seller, "100.00")
    sign_off_details(client)
    assert client.post(reverse("orders:review")).status_code == 302
    placed = VendorOrder.objects.get(vendor=seller)
    assert placed.commission_snapshot == own.snapshot()
    assert placed.commission_snapshot["percent"] == "12.50"


def test_no_rule_at_all_keeps_ordering_closed(client: Client) -> None:
    cart = with_cart(client)
    add(cart, vendor(), "10.00")
    sign_off_details(client)
    html = client.get(reverse("orders:review")).content.decode()
    assert "Ordering is not open yet" in html
    assert "Place order" not in html
    client.post(reverse("orders:review"))
    assert not Order.objects.exists()


def test_a_vendor_without_a_rule_is_left_out_and_the_others_get_theirs() -> None:
    """Only a vendor rule, no default: the other vendor has nothing, so it is missing."""
    covered, uncovered = vendor("Gamma Shop", shipping=False), vendor("Delta", shipping=False)
    own = CommissionRuleFactory.create(vendor=covered, valid_from=utc(2026, 1, 1), valid_to=None)
    snapshots = state.commission_snapshots([covered.pk, uncovered.pk])
    assert snapshots == {covered.pk: own.snapshot()}


def test_the_snapshot_has_exactly_the_keys_place_order_accepts() -> None:
    default_rule()
    seller = vendor(shipping=False)
    snapshot = state.commission_snapshots([seller.pk])[seller.pk]
    assert set(COMMISSION_KEYS) <= set(snapshot)
    assert set(snapshot) <= set(COMMISSION_KEYS) | set(COMMISSION_OPTIONAL_KEYS)


def test_a_rule_that_starts_later_does_not_open_ordering_today() -> None:
    CommissionRuleFactory.create(valid_from=utc(2099, 1, 1), valid_to=None)
    seller = vendor(shipping=False)
    assert state.commission_snapshots([seller.pk]) == {}
