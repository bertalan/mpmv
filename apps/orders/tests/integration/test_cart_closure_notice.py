"""VEN.20 slice 2: the closure notice of each cart package costs the same queries for the whole
cart, whatever its number of vendors, and has its own heading id."""

import datetime as dt
import re

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.vendors.tests.factories import VendorClosureFactory

from .test_checkout_views import add, tax_setup, vendor, with_cart  # noqa: F401

pytestmark = pytest.mark.django_db


def _closure_queries(client: Client) -> tuple[int, str]:
    with CaptureQueriesContext(connection) as queries:
        html = client.get(reverse("orders:cart")).content.decode()
    count = sum("vendors_vendorclosure" in q["sql"] for q in queries.captured_queries)
    return count, html


def _closed_vendor(name: str):  # type: ignore[no-untyped-def]
    found = vendor(name)
    today = dt.date.today()
    VendorClosureFactory.create(
        vendor=found,
        start_date=today + dt.timedelta(days=3),
        end_date=today + dt.timedelta(days=9),
    )
    return found


def test_the_closure_query_count_is_the_same_for_one_and_for_three_vendors(
    client: Client,
) -> None:
    cart = with_cart(client)
    add(cart, _closed_vendor("Alpha Shop"), "30.00", "Blue vase")
    one, _ = _closure_queries(client)
    add(cart, _closed_vendor("Beta Shop"), "20.00", "Red cup")
    add(cart, _closed_vendor("Gamma Shop"), "10.00", "Green jug")
    three, html = _closure_queries(client)
    # One read for the notices and one for the checkout's dispatch dates (VEN.20 slice 3a).
    assert one == three == 2
    assert html.count("closure-notice__text") == 3


def test_every_notice_has_its_own_heading_id_that_the_region_points_to(client: Client) -> None:
    cart = with_cart(client)
    add(cart, _closed_vendor("Alpha Shop"), "30.00", "Blue vase")
    add(cart, _closed_vendor("Beta Shop"), "20.00", "Red cup")
    html = client.get(reverse("orders:cart")).content.decode()
    ids = re.findall(r'<h2 id="(closure-notice-[^"]+)"', html)
    labelled = re.findall(r'aria-labelledby="(closure-notice-[^"]+)"', html)
    assert len(ids) == 2
    assert len(set(ids)) == 2
    assert sorted(labelled) == sorted(ids)
