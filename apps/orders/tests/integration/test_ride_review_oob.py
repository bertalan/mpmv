"""THM.27 (2/2) at view level: a real shipping change on the `ride` review page answers an
out-of-band summary and pay button that carry the same, updated total."""

import re
from decimal import Decimal
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.orders.tests.integration.test_checkout_views import (  # noqa: F401 (fixtures)
    add,
    commission,
    sign_off_details,
    tax_setup,
    vendor,
    with_cart,
)
from apps.shipping.models import ShippingMethod
from apps.shipping.tests.factories import (
    ShippingMethodFactory,
    ShippingMethodTranslationFactory,
    WeightBandFactory,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("english_interface", "commission")]


@pytest.fixture(autouse=True)
def _ride(settings: Any) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"


def _summary_total(html: str) -> str:
    aside = html[html.index('id="review-summary"') :]
    return re.search(r"order-summary__total\">Total</dt><dd[^>]*>([^<]+)</dd>", aside).group(1)  # type: ignore[union-attr]


def test_a_shipping_change_swaps_the_summary_and_the_pay_button_with_the_same_total(
    client: Client,
) -> None:
    cart = with_cart(client)
    found = vendor("Alpha Shop")
    zone = ShippingMethod.objects.get(vendor=found).zone
    express = ShippingMethodFactory.create(vendor=found, zone=zone, express=True)
    ShippingMethodTranslationFactory.create(method=express, name="Express", status="published")
    WeightBandFactory.create(
        method=express, from_kg=Decimal("0"), to_kg=Decimal("5"), cost_net=Decimal("30.00")
    )
    add(cart, found, "100.00")
    sign_off_details(client)
    page = client.get(reverse("orders:review")).content.decode()
    keys = re.findall(r'<input type="radio"[^>]*value="([^"]+)"', page)
    assert len(keys) == 2
    before = _summary_total(page)
    # The first option is the one the review preselects: choose the second.
    other = keys[1]

    fragment = client.post(
        reverse("orders:shipping"), {f"ship_{found.public_id}": other}, HTTP_HX_REQUEST="true"
    ).content.decode()

    assert 'id="review-summary"' in fragment
    assert re.search(r'id="review-summary"[^>]*hx-swap-oob="true"', fragment)
    assert 'id="review-actions" hx-swap-oob="true"' in fragment
    after = _summary_total(fragment)
    assert after != before
    pay = re.search(
        r'<button type="submit" class="btn btn-primary btn-lg">([^<]+)</button>', fragment
    )
    assert pay is not None, fragment[fragment.index("review-actions") :][:1500]
    amount = re.search(r"\d+[.,]\d{2}", after)
    assert amount is not None
    assert amount.group(0) in pay.group(1), "the button carries the summary total"
    assert 'id="order-summary"' not in fragment.split('id="review-summary"')[0], "summary only once"
