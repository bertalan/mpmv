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


def test_a_failed_second_order_in_the_same_session_still_retries(
    client: Client,
    commission: None,  # noqa: F811
    stripe: FakeStripeClient,  # noqa: F811
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.STRIPE_PUBLISHABLE_KEY = "pk_" + secrets.token_urlsafe(12)
    shop = vendor("Alpha Shop")
    add(with_cart(client), shop, "100.00")
    sign_off_details(client)
    assert client.post(reverse("orders:review")).status_code == 302
    first = Order.objects.get()
    assert client.session["checkout_payment_ref"] == first.payment_intent_ref
    real = stripe.create_payment_intent
    monkeypatch.setattr(stripe, "create_payment_intent", failing)
    add(with_cart(client), shop, "50.00")
    sign_off_details(client)
    assert client.post(reverse("orders:review")).status_code == 302
    second = Order.objects.exclude(pk=first.pk).get()
    assert second.payment_intent_ref == ""
    assert "checkout_payment_ref" not in client.session
    monkeypatch.setattr(stripe, "create_payment_intent", real)
    response = client.get(reverse("orders:confirmation"))
    assert response.status_code == 200
    assert "payment-form" in response.content.decode()


@pytest.mark.parametrize("status", ["processing", "succeeded"])
def test_the_page_says_payment_in_progress_when_the_customer_already_paid(
    client: Client,
    commission: None,  # noqa: F811
    stripe: FakeStripeClient,  # noqa: F811
    settings: Any,
    status: str,
) -> None:
    """PAY.2 review point: coming back before the webhook shows no second form."""
    import dataclasses

    settings.STRIPE_PUBLISHABLE_KEY = "pk_" + secrets.token_urlsafe(12)
    add(with_cart(client), vendor("Alpha Shop"), "100.00")
    sign_off_details(client)
    client.post(reverse("orders:review"))
    order = Order.objects.get()
    intent = stripe.intents[order.payment_intent_ref]
    stripe.intents[intent.id] = dataclasses.replace(intent, status=status)
    html = client.get(reverse("orders:confirmation")).content.decode()
    assert "Payment received, confirming" in html
    assert "payment-form" not in html
