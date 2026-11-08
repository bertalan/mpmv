"""EVT.1 part 2/2: who sees an event on the public site (spec 15 §Status).

Public queries need a live, public, not hidden event of an active vendor: the index leaves out
the others, and their page is a 404 (the same rule as a product of a suspended vendor).
"""

import datetime as dt

import pytest
from django.test import Client

from apps.events.models import EventPage
from apps.events.tests.factories import EventPageFactory, primary_index
from apps.vendors.models import Vendor

pytestmark = pytest.mark.django_db


def _published(**kwargs: object) -> EventPage:
    page: EventPage = EventPageFactory.create(**kwargs)
    page.save_revision().publish()
    page.refresh_from_db()
    return page


@pytest.mark.parametrize("status", [Vendor.Status.SUSPENDED, Vendor.Status.CLOSED])
def test_an_inactive_vendors_event_is_not_listed(client: Client, status: str) -> None:
    shown = _published(title="Open fair")
    gone = _published(title="Gone fair")
    assert gone.vendor is not None
    Vendor.objects.filter(pk=gone.vendor.pk).update(status=status)
    index = primary_index()
    assert [page.pk for page in index.events()] == [shown.pk]
    html = client.get(index.url).content.decode()
    assert "Open fair" in html
    assert "Gone fair" not in html


@pytest.mark.parametrize("status", [Vendor.Status.SUSPENDED, Vendor.Status.CLOSED])
def test_an_inactive_vendors_event_page_is_a_404(client: Client, status: str) -> None:
    page = _published()
    assert page.vendor is not None
    url = page.url
    assert client.get(url).status_code == 200
    Vendor.objects.filter(pk=page.vendor.pk).update(status=status)
    assert client.get(url).status_code == 404


def test_a_platform_event_has_no_vendor_to_check(client: Client) -> None:
    page = _published(platform=True, title="Platform fair")
    assert client.get(page.url).status_code == 200
    assert [event.pk for event in primary_index().events()] == [page.pk]


def test_a_hidden_event_page_is_a_404(client: Client) -> None:
    page = _published()
    EventPage.objects.filter(pk=page.pk).update(hidden_at=dt.datetime(2027, 2, 1, tzinfo=dt.UTC))
    assert client.get(page.url).status_code == 404
