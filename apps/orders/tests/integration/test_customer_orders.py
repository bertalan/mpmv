"""ORD.7: the customer area "My orders". A customer sees their own orders only (a stranger's
order and a guest order are a 404), each vendor part with a status timeline and tracking, and
only orders that are still `active` (D-30): a legacy order the vendor's migration choice kept
(D-16) shows, one in the restricted tax archive does not."""

import re
import secrets
import uuid
from typing import Any

import pytest
from django.db import models
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.tests.factories import UserFactory
from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.orders import customer_views, services
from apps.orders.models import Order, VendorOrder
from apps.orders.tests.integration.test_retention import place
from apps.shipping.tests.factories import CarrierFactory

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_label="system:test", source=AuditLog.Source.SYSTEM)


def signed_in() -> tuple[Client, User]:
    user = UserFactory.create(password=secrets.token_urlsafe(16))
    client = Client()
    client.force_login(user)
    return client, user


def paid_order(user: User | None, **overrides: Any) -> Order:
    order = place(customer=user, state="awaiting_payment", **overrides)
    services.transition_order(order, "paid", ctx=CTX)
    return order


def sub_of(order: Order) -> VendorOrder:
    return order.vendor_orders.get()


def detail_url(order: Order) -> str:
    return reverse("customer_order", args=[order.public_id])


# --- access and isolation ----------------------------------------------------------------------


@pytest.mark.parametrize("name", ["customer_orders"])
def test_anonymous_visitors_are_sent_to_sign_in(name: str) -> None:
    response = Client().get(reverse(name))
    assert response.status_code == 302
    assert "login" in response["Location"]


def test_the_detail_of_an_anonymous_visitor_is_a_sign_in_redirect() -> None:
    order = paid_order(UserFactory.create())
    response = Client().get(detail_url(order))
    assert response.status_code == 302


def test_the_list_shows_only_the_callers_orders() -> None:
    client, user = signed_in()
    mine = paid_order(user)
    other = paid_order(UserFactory.create())
    guest = paid_order(None)
    body = client.get(reverse("customer_orders")).content.decode()
    assert mine.number in body
    assert other.number not in body
    assert guest.number not in body


def test_a_stranger_s_order_is_a_404_like_an_unknown_one() -> None:
    client, _user = signed_in()
    other = paid_order(UserFactory.create())
    assert client.get(detail_url(other)).status_code == 404
    assert client.get(reverse("customer_order", args=[uuid.uuid4()])).status_code == 404


def test_a_guest_order_is_a_404_even_with_the_same_email() -> None:
    client, user = signed_in()
    guest = paid_order(None, email=user.email)
    assert client.get(detail_url(guest)).status_code == 404


def test_the_list_is_empty_with_a_next_step_for_a_new_customer() -> None:
    client, _user = signed_in()
    response = client.get(reverse("customer_orders"))
    assert response.status_code == 200
    assert "You have no orders yet" in response.content.decode()


def test_a_draft_in_checkout_is_not_listed() -> None:
    client, user = signed_in()
    draft = place(customer=user, state="checkout")
    assert draft.number not in client.get(reverse("customer_orders")).content.decode()
    assert client.get(detail_url(draft)).status_code == 404


def test_the_pages_are_not_indexed_and_not_cached() -> None:
    client, user = signed_in()
    order = paid_order(user)
    for url in (reverse("customer_orders"), detail_url(order)):
        response = client.get(url)
        assert 'content="noindex, nofollow"' in response.content.decode()
        assert "no-store" in response["Cache-Control"]


def test_the_list_is_paginated(monkeypatch: pytest.MonkeyPatch) -> None:
    client, user = signed_in()
    numbers = [paid_order(user).number for _ in range(3)]
    monkeypatch.setattr(customer_views, "PAGE_SIZE", 2)
    first = client.get(reverse("customer_orders")).content.decode()
    second = client.get(reverse("customer_orders"), {"page": 2}).content.decode()
    on_first = [n for n in numbers if n in first]
    assert len(on_first) == 2
    assert [n for n in numbers if n in second] == [n for n in numbers if n not in on_first]
    assert 'rel="next"' in first


# --- detail: parts, timeline, tracking ---------------------------------------------------------


def test_the_detail_shows_each_vendor_part_with_its_lines_and_timeline() -> None:
    client, user = signed_in()
    order = paid_order(user)
    body = client.get(detail_url(order)).content.decode()
    assert order.number in body
    assert sub_of(order).vendor.display_name in body
    assert "Cup" in body
    assert "12.20 EUR" in body  # 10.00 net + 22% VAT, with VAT, as the customer pays it
    assert 'aria-current="step"' in body
    assert "Payment received" in body
    assert "Waiting for the seller" in body


def test_a_shipped_part_shows_carrier_number_and_a_link_from_the_carrier_record() -> None:
    client, user = signed_in()
    order = paid_order(user)
    sub = sub_of(order)
    CarrierFactory.create(
        code="fast-courier", tracking_url_template="https://t.example.com/{tracking_number}"
    )
    services.transition_vendor_order(sub, "accepted", ctx=CTX)
    services.transition_vendor_order(
        sub, "shipped", ctx=CTX, tracking_number="AB123", carrier="Fast Courier"
    )
    body = client.get(detail_url(order)).content.decode()
    assert "Fast Courier" in body
    assert "AB123" in body
    assert 'href="https://t.example.com/AB123"' in body
    assert re.search(r"timeline__step--current[^>]*aria-current=\"step\"", body)
    assert "Shipped" in body


def test_a_carrier_without_a_record_shows_the_number_without_a_link() -> None:
    client, user = signed_in()
    order = paid_order(user)
    sub = sub_of(order)
    services.transition_vendor_order(sub, "accepted", ctx=CTX)
    services.transition_vendor_order(
        sub, "shipped", ctx=CTX, tracking_number="ZZ999", carrier="Own van"
    )
    body = client.get(detail_url(order)).content.decode()
    assert "ZZ999" in body
    assert "Own van" in body
    assert "Track the parcel" not in body


def test_an_unsafe_carrier_template_gives_no_link() -> None:
    client, user = signed_in()
    order = paid_order(user)
    sub = sub_of(order)
    CarrierFactory.create(code="bad", tracking_url_template="javascript:alert({tracking_number})")
    services.transition_vendor_order(sub, "accepted", ctx=CTX)
    services.transition_vendor_order(
        sub, "shipped", ctx=CTX, tracking_number="AB123", carrier="Bad"
    )
    body = client.get(detail_url(order)).content.decode()
    assert "javascript:" not in body
    assert "AB123" in body


def test_a_delivered_part_shows_the_delivery_step() -> None:
    client, user = signed_in()
    order = paid_order(user)
    sub = sub_of(order)
    services.transition_vendor_order(sub, "accepted", ctx=CTX)
    services.transition_vendor_order(sub, "shipped", ctx=CTX, tracking_number="T1")
    services.transition_vendor_order(sub, "delivered", ctx=CTX)
    body = client.get(detail_url(order)).content.decode()
    assert body.count("timeline__step--done") == 4
    assert "timeline__step--current" in body


def test_internal_vendor_and_money_data_never_reach_the_customer() -> None:
    client, user = signed_in()
    order = paid_order(user)
    VendorOrder.objects.filter(pk=sub_of(order).pk).update(
        support_notes="internal note xyz", carrier_contact_phone="+39 000 111"
    )
    Order.objects.filter(pk=order.pk).update(payment_intent_ref="pi_secret_ref")
    body = client.get(detail_url(order)).content.decode()
    for hidden in ("internal note xyz", "+39 000 111", "pi_secret_ref", "commission"):
        assert hidden not in body


# --- legacy orders and the restricted archive ----------------------------------------------------


def test_a_legacy_order_kept_active_is_listed_and_open() -> None:
    client, user = signed_in()
    legacy = place(customer=user, state="closed", legacy_wp_order_id=4711)
    assert legacy.number in client.get(reverse("customer_orders")).content.decode()
    assert client.get(detail_url(legacy)).status_code == 200


def test_a_legacy_order_in_the_restricted_archive_is_hidden_from_list_and_detail() -> None:
    """The vendor's migration choice (D-16) decides what stays active; the rest of the ten
    years is `restricted_tax_only`, readable only through the Tax/Audit permission."""
    client, user = signed_in()
    archived = place(customer=user, state="closed", legacy_wp_order_id=4712)
    services.restrict_order(archived, ctx=CTX, reason="account_erasure")
    archived.refresh_from_db()
    assert archived.privacy_status == "restricted_tax_only"
    assert archived.number not in client.get(reverse("customer_orders")).content.decode()
    assert client.get(detail_url(archived)).status_code == 404


@pytest.mark.skipif(
    not any(f.name == "tax_archive" for f in Order._meta.get_fields()),
    reason="the restricted tax archive flag (ORD.14, #338) is not on main yet",
)
def test_a_row_flagged_as_tax_archive_is_hidden_whatever_its_privacy_status() -> None:
    client, user = signed_in()
    legacy = place(customer=user, state="closed", legacy_wp_order_id=4713)
    Order.objects.filter(pk=legacy.pk).update(**{"tax_archive": True})
    assert legacy.number not in client.get(reverse("customer_orders")).content.decode()
    assert client.get(detail_url(legacy)).status_code == 404


def test_the_service_queryset_is_empty_for_an_anonymous_user() -> None:
    from django.contrib.auth.models import AnonymousUser

    assert not services.customer_orders(AnonymousUser()).exists()  # type: ignore[arg-type]
    assert isinstance(services.customer_orders(UserFactory.create()), models.QuerySet)
