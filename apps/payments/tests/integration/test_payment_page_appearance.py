"""THM.8: the payment page passes the theme's Stripe appearance as a data attribute (no inline
script, so the CSP is untouched); payment.js hands it to `stripe.elements`."""

import json
import secrets
from html import unescape
from pathlib import Path
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.orders.tests.integration.test_place_order import place, sellable
from apps.payments.tests.fake_stripe import FakeStripeClient
from apps.payments.tests.integration.test_order_payment import stripe  # noqa: F401 (fixture)

pytestmark = pytest.mark.django_db


def page(client: Client, settings: Any, theme: str, stripe: FakeStripeClient) -> str:  # noqa: F811
    settings.MPMV_THEME_DEFAULT = theme
    settings.STRIPE_PUBLISHABLE_KEY = "pk_" + secrets.token_urlsafe(12)
    order = place(sellable())
    session = client.session
    session["checkout_confirmation"] = str(order.public_id)
    session.save()
    return client.get(reverse("orders:confirmation")).content.decode()


def appearance(html: str) -> dict[str, Any]:
    start = html.index('data-appearance="') + len('data-appearance="')
    return json.loads(unescape(html[start : html.index('"', start)]))  # type: ignore[no-any-return]


def test_the_ride_payment_page_carries_the_ride_appearance(
    client: Client,
    settings: Any,
    stripe: FakeStripeClient,  # noqa: F811
) -> None:
    value = appearance(page(client, settings, "ride", stripe))
    assert value["variables"]["colorPrimary"] == "#046BD2"
    assert "Barlow" in value["variables"]["fontFamily"]
    assert value["variables"]["borderRadius"] == "4px"


def test_payment_js_passes_the_appearance_to_stripe_elements() -> None:
    source = Path("apps/orders/static/orders/payment.js").read_text(encoding="utf-8")
    assert "dataset.appearance" in source
    assert "appearance" in source.split("stripe.elements")[1].split(")")[0]
