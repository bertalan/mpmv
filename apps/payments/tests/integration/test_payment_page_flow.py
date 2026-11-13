"""ORD.4/PAY.1: the checkout review to the payment page, including the retry after a failed call
and the page of an order that is no longer open."""

import secrets
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from apps.orders.models import Order
from apps.orders.tests.integration.test_checkout_views import (  # noqa: F401 (fixtures)
    add,
    commission,
    sign_off_details,
    tax_setup,
    vendor,
    with_cart,
)
from apps.payments.stripe_client import StripeError
from apps.payments.tests.fake_stripe import FakeStripeClient
from apps.payments.tests.integration.test_order_payment import stripe  # noqa: F401 (fixture)

pytestmark = pytest.mark.django_db


def failing(**kwargs: Any) -> Any:
    raise StripeError("down")


def test_a_failed_first_call_then_a_retry_on_the_payment_page_works(
    client: Client,
    commission: None,  # noqa: F811
    stripe: FakeStripeClient,  # noqa: F811
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.STRIPE_PUBLISHABLE_KEY = "pk_" + secrets.token_urlsafe(12)
    real = stripe.create_payment_intent
    monkeypatch.setattr(stripe, "create_payment_intent", failing)
    cart = with_cart(client)
    add(cart, vendor("Alpha Shop"), "100.00")
    sign_off_details(client)
    assert client.post(reverse("orders:review")).status_code == 302
    order = Order.objects.get()
    assert order.payment_intent_ref == ""
    assert not client.session.get("checkout_payment_ref")  # no empty id remembered
    monkeypatch.setattr(stripe, "create_payment_intent", real)
    response = client.get(reverse("orders:confirmation"))
    assert response.status_code == 200
    assert "payment-form" in response.content.decode()
    order.refresh_from_db()
    assert order.payment_intent_ref
    assert client.session["checkout_payment_ref"] == order.payment_intent_ref


def test_a_cancelled_order_is_not_shown_as_paid(
    client: Client,
    commission: None,  # noqa: F811
    stripe: FakeStripeClient,  # noqa: F811
    settings: Any,
) -> None:
    settings.STRIPE_PUBLISHABLE_KEY = "pk_" + secrets.token_urlsafe(12)
    cart = with_cart(client)
    add(cart, vendor("Alpha Shop"), "100.00")
    sign_off_details(client)
    client.post(reverse("orders:review"))
    Order.objects.update(state="cancelled")
    html = client.get(reverse("orders:confirmation")).content.decode()
    assert "Payment received" not in html
    assert "no longer open for payment" in html
    assert "payment-form" not in html
