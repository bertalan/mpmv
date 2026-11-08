"""ORD.1: the cart of the current request, and the guest-to-user merge when the visitor signs in.

Django changes the session key on sign-in (`cycle_key`), so the guest cart is remembered in the
session data, which survives the change.
"""

import logging
from decimal import Decimal

import pytest
from django.conf import settings
from django.contrib.auth import login, logout
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.db import DatabaseError
from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.cart import services
from apps.cart.models import Cart
from apps.cart.session import GUEST_CART_SESSION_KEY, current_cart
from apps.catalog.models import Product
from apps.catalog.tests.factories import ProductFactory
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory

pytestmark = pytest.mark.django_db

BACKEND = "django.contrib.auth.backends.ModelBackend"


def visitor_request(language: str = "en") -> HttpRequest:
    request = RequestFactory().get("/")
    SessionMiddleware(lambda r: HttpResponse()).process_request(request)
    request.user = AnonymousUser()
    request.LANGUAGE_CODE = language
    return request


def sellable() -> Product:
    return ProductFactory.create(
        vendor=VendorFactory.create(status=Vendor.Status.ACTIVE),
        status=Product.Status.PUBLISHED,
        stock_qty=10,
        price_net=Decimal("10.00"),
    )


def sign_in(request: HttpRequest, user: User) -> None:
    login(request, user, backend=BACKEND)


def test_a_visitor_has_no_cart_until_one_is_needed() -> None:
    request = visitor_request()
    assert current_cart(request) is None
    assert not Cart.objects.exists()

    cart = current_cart(request, create=True)
    assert cart is not None
    assert (cart.user, cart.session_key) == (None, request.session.session_key)
    assert current_cart(request) == cart


def test_a_guest_cart_takes_the_visitors_language() -> None:
    cart = current_cart(visitor_request(language="it"), create=True)
    assert cart is not None
    assert cart.language == "it"


def test_signing_in_merges_the_guest_cart_into_the_users_cart() -> None:
    user = UserFactory.create()
    product, other = sellable(), sellable()
    services.add_to_cart(services.get_or_create_cart(user=user), product, quantity=1)

    request = visitor_request()
    guest = current_cart(request, create=True)
    assert guest is not None
    services.add_to_cart(guest, product, quantity=2)
    services.add_to_cart(guest, other, quantity=1)
    old_key = request.session.session_key

    sign_in(request, user)

    assert request.session.session_key != old_key  # Django rotated the key
    cart = current_cart(request)
    assert cart is not None
    assert cart.user == user
    assert {line.product_id: line.quantity for line in cart.lines.all()} == {
        product.pk: 3,
        other.pk: 1,
    }
    assert Cart.objects.count() == 1


def test_signing_in_without_a_guest_cart_creates_nothing() -> None:
    request = visitor_request()
    sign_in(request, UserFactory.create())
    assert not Cart.objects.exists()


def same_session_request(request: HttpRequest) -> HttpRequest:
    """A second request carrying the same session cookie, its session data loaded now."""
    request.session.save()
    twin = RequestFactory().get("/")
    middleware = SessionMiddleware(lambda r: HttpResponse())
    twin.COOKIES[settings.SESSION_COOKIE_NAME] = request.session.session_key or ""
    middleware.process_request(twin)
    assert twin.session.get(GUEST_CART_SESSION_KEY)  # loads the session data now
    twin.user = AnonymousUser()
    twin.LANGUAGE_CODE = "en"
    return twin


@pytest.mark.parametrize("user_has_a_cart", [False, True])
def test_a_double_submitted_sign_in_merges_the_guest_cart_once(user_has_a_cart: bool) -> None:
    user = UserFactory.create()
    product = sellable()
    if user_has_a_cart:
        services.add_to_cart(services.get_or_create_cart(user=user), product, quantity=1)
    request = visitor_request()
    guest = current_cart(request, create=True)
    assert guest is not None
    services.add_to_cart(guest, product, quantity=2)
    twin = same_session_request(request)  # loaded before the first sign-in rotates the key

    sign_in(request, user)
    sign_in(twin, user)

    cart = Cart.objects.get()
    assert cart.user == user
    assert {line.product_id: line.quantity for line in cart.lines.all()} == {
        product.pk: 3 if user_has_a_cart else 2
    }


def test_a_failed_merge_does_not_block_signing_in(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def failing_merge(*args: object, **kwargs: object) -> None:
        raise DatabaseError("simulated failure")

    monkeypatch.setattr(services, "merge_carts", failing_merge)
    user = UserFactory.create()
    request = visitor_request()
    guest = current_cart(request, create=True)
    assert guest is not None

    with caplog.at_level(logging.ERROR, logger="apps.cart.session"):
        sign_in(request, user)

    assert request.user == user
    assert "guest cart" in caplog.text
    assert Cart.objects.get() == guest  # left as it was


def test_a_signed_in_users_cart_survives_signing_out() -> None:
    user = UserFactory.create()
    request = visitor_request()
    guest = current_cart(request, create=True)
    assert guest is not None
    services.add_to_cart(guest, sellable())
    sign_in(request, user)

    logout(request)
    assert current_cart(request) is None
    assert Cart.objects.get().user == user
