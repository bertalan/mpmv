"""EVT.6: the ride theme has no event templates of its own yet (ride gets them in its own task), so
it renders the base event pages through the theme chain."""

import datetime as dt

import pytest
from django.apps import apps
from django.test import Client
from django.utils import timezone
from wagtail.models import Site

from apps.events.tests.factories import EventPageFactory, primary_index
from apps.themes.services import use_theme

pytestmark = pytest.mark.django_db


def test_ride_renders_the_event_list_calendar_archive_and_page(client: Client) -> None:
    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )
    start = timezone.now() + dt.timedelta(days=10)
    page = EventPageFactory.create(
        title="Ride fair", starts_at=start, ends_at=start + dt.timedelta(hours=8)
    )
    page.save_revision().publish()
    page.refresh_from_db()
    index = primary_index().url
    with use_theme("ride"):
        assert "Ride fair" in client.get(str(page.url)).content.decode()
        assert "Ride fair" in client.get(str(index)).content.decode()
        month = f"{index}calendar/{start:%Y-%m}/"
        assert "<table" in client.get(month).content.decode()
        assert client.get(f"{index}past/").status_code == 200
