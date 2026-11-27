"""VEN.20 slice 3a: the checkout moves the dispatch date of a closed vendor's package, the order
keeps it (regression case 11 of spec 04 §4.4) and the order emails show it (ORD.9).

Done-when: a checkout with a closed and an open vendor shows the notice and the shifted date only
on the closed vendor's package, still places the order, and each vendor order keeps its date.
"""

import datetime as dt
import re

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import formats, translation

from apps.cart.tests.factories import CartFactory
from apps.core.models import OutboxEmail
from apps.orders import checkout, notifications, services
from apps.orders.models import Order
from apps.orders.tests.integration.test_services import CTX
from apps.shipping.models import ShippingMethod
from apps.vendors.models import Vendor, VendorClosure
from apps.vendors.services import business_today
from apps.vendors.tests.factories import VendorClosureFactory

from .test_checkout_views import (  # noqa: F401
    add,
    commission,
    post_review,
    sign_off_details,
    tax_setup,
    vendor,
    with_cart,
)

pytestmark = pytest.mark.django_db

ADDRESS = {"line1": "1 Via Test", "city": "Roma", "postcode": "00100", "country": "IT"}


def _long(day: dt.date, language: str = "en") -> str:
    with translation.override(language):
        return str(formats.date_format(day, "DATE_FORMAT"))


def _packages(html: str) -> dict[str, str]:
    """The page from each package section to the next, by the vendor it is sold by."""
    sections = html.split('<section class="package"')[1:]
    return {re.search(r"Sold by ([^<]+)<", s).group(1): s for s in sections}  # type: ignore[union-attr]


def _two_vendor_checkout(client: Client) -> tuple[Vendor, Vendor, VendorClosure, dt.date]:
    """Alpha Shop is closed from two days ago to five days ahead, Beta Shop is open."""
    today = business_today()
    closed, open_ = vendor("Alpha Shop"), vendor("Beta Shop")
    ShippingMethod.objects.filter(vendor=closed).update(min_delivery_days=2, max_delivery_days=4)
    closure = VendorClosureFactory.create(
        vendor=closed,
        start_date=today - dt.timedelta(days=2),
        end_date=today + dt.timedelta(days=5),
    )
    cart = with_cart(client)
    add(cart, closed, "30.00", "Blue vase")
    add(cart, open_, "20.00", "Red cup")
    sign_off_details(client)
    return closed, open_, closure, today


@pytest.mark.usefixtures("commission")
def test_only_the_closed_package_is_shifted_and_the_order_is_still_placed(client: Client) -> None:
    closed, open_, _, today = _two_vendor_checkout(client)
    dispatch = today + dt.timedelta(days=6)
    packages = _packages(client.get(reverse("orders:review")).content.decode())

    alpha, beta = packages["Alpha Shop"], packages["Beta Shop"]
    assert "closure-notice__text" in alpha
    assert _long(dispatch) in alpha
    # The delivery estimate counts from the dispatch date (`delivery_window`, 2 to 4 days).
    assert _long(dispatch + dt.timedelta(days=2)) in alpha
    assert _long(dispatch + dt.timedelta(days=4)) in alpha
    assert "closure-notice" not in beta
    assert _long(dispatch) not in beta

    # No extra confirmation step: the order is placed as usual.
    response = post_review(client, {"accept_terms": "on"})
    assert response["Location"] == reverse("orders:confirmation")
    order = Order.objects.get()
    stored = {sub.vendor_id: sub.expected_dispatch_on for sub in order.vendor_orders.all()}
    assert stored == {closed.pk: dispatch, open_.pk: today}


@pytest.mark.usefixtures("commission")
def test_the_snapshot_survives_an_edit_or_a_deletion_of_the_closure(client: Client) -> None:
    closed, _, closure, today = _two_vendor_checkout(client)
    post_review(client, {"accept_terms": "on"})
    sub = Order.objects.get().vendor_orders.get(vendor=closed)
    VendorClosure.objects.filter(pk=closure.pk).update(end_date=today + dt.timedelta(days=20))
    sub.refresh_from_db()
    assert sub.expected_dispatch_on == today + dt.timedelta(days=6)
    VendorClosure.objects.filter(pk=closure.pk).delete()
    sub.refresh_from_db()
    assert sub.expected_dispatch_on == today + dt.timedelta(days=6)


@pytest.mark.usefixtures("commission")
def test_the_order_emails_show_the_shifted_date_only_for_the_closed_vendor(client: Client) -> None:
    closed, _, closure, today = _two_vendor_checkout(client)
    post_review(client, {"accept_terms": "on"})
    order = Order.objects.get()
    closure.delete()  # the emails read the snapshot, never the schedule
    services.transition_order(order, "paid", ctx=CTX)
    notifications.notify_order_paid(order)
    dispatch = today + dt.timedelta(days=6)

    confirmations = {
        mail.context_data["vendor_name"]: mail
        for mail in OutboxEmail.objects.filter(template_key=notifications.ORDER_CONFIRMATION)
    }
    alpha, beta = confirmations["Alpha Shop"], confirmations["Beta Shop"]
    assert _long(dispatch, alpha.language) in alpha.text_body
    assert _long(dispatch, alpha.language) in alpha.html_body
    assert "dispatched from" not in beta.text_body

    to_vendor = OutboxEmail.objects.get(
        template_key=notifications.VENDOR_NEW_ORDER, recipient=closed.contact_email
    )
    assert _long(dispatch, to_vendor.language) in to_vendor.text_body
    assert _long(dispatch, to_vendor.language) in to_vendor.html_body
    other = OutboxEmail.objects.filter(template_key=notifications.VENDOR_NEW_ORDER).exclude(
        recipient=closed.contact_email
    )
    assert all(_long(dispatch, m.language) not in m.text_body for m in other)


def test_today_is_the_business_day_of_the_checkout_instant() -> None:
    """23:30 UTC on 31 July 2026 is already 1 August in the business time zone (Europe/Rome):
    inside the closure, so the package leaves on 16 August; the open vendor leaves that day."""
    closed, open_ = vendor("Alpha Shop"), vendor("Beta Shop")
    VendorClosureFactory.create(
        vendor=closed, start_date=dt.date(2026, 8, 1), end_date=dt.date(2026, 8, 15)
    )
    cart = CartFactory.create()
    add(cart, closed, "30.00", "Blue vase")
    add(cart, open_, "20.00", "Red cup")
    now = dt.datetime(2026, 7, 31, 23, 30, tzinfo=dt.UTC)
    preview = checkout.build_checkout(cart, ADDRESS, now=now)
    dates = {p.vendor_id: p.dispatch_on for p in preview.packages}
    assert dates == {closed.pk: dt.date(2026, 8, 16), open_.pk: dt.date(2026, 8, 1)}
    placed = {v.vendor.pk: v.expected_dispatch_on for v in preview.vendors}
    assert placed == dates


def test_a_chain_of_closures_past_day_180_moves_the_dispatch_to_its_end() -> None:
    """The second range starts on day 181, right after the first: one closure of 200 days."""
    seller = vendor("Alpha Shop")
    today = business_today()
    VendorClosureFactory.create(
        vendor=seller, start_date=today - dt.timedelta(days=1), end_date=today + dt.timedelta(180)
    )
    VendorClosureFactory.create(
        vendor=seller,
        start_date=today + dt.timedelta(days=181),
        end_date=today + dt.timedelta(days=200),
    )
    cart = CartFactory.create()
    add(cart, seller, "30.00", "Blue vase")
    (package,) = checkout.build_checkout(cart, ADDRESS).packages
    assert package.dispatch_on == today + dt.timedelta(days=201)
