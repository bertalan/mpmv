"""FIT.11 (2/2): the *Only events for my garage* checkbox and the feeds that ignore the garage
(spec 17 §17.5.5). The checkbox shows only with an active garage, is off by default, and filters
the list to the events tagged for the vehicle; the RSS and ICS feeds are byte-identical with and
without a garage, and with `?garage=1`."""

import datetime as dt
import re

import pytest
from django.apps import apps
from django.test import Client
from django.urls import reverse
from django.utils import timezone, translation

from apps.core.i18n import primary_language
from apps.events.models import EventPage
from apps.events.tests.factories import EventFitmentFactory, EventPageFactory, primary_index
from apps.fitment.tests.factories import FitmentNodeFactory

pytestmark = pytest.mark.django_db

CHECKBOX = 'id="event-filter-garage"'


@pytest.fixture(autouse=True)
def _indexing_on() -> None:
    from wagtail.models import Site

    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


def _event(days: int, title: str) -> EventPage:
    start = (timezone.now() + dt.timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
    page = EventPageFactory.create(
        title=title, starts_at=start, ends_at=start + dt.timedelta(hours=8)
    )
    page.save_revision().publish()
    page.refresh_from_db()
    return page


@pytest.fixture
def events() -> dict[str, EventPage]:
    make = FitmentNodeFactory.create(name="Acme")
    roadster = FitmentNodeFactory.create(name="Roadster", parent=make)
    tourer = FitmentNodeFactory.create(name="Tourer", parent=make)
    mine = _event(10, "Roadster meet")
    other = _event(11, "Tourer meet")
    general = _event(12, "Open meet")
    EventFitmentFactory.create(page=mine, node=roadster)
    EventFitmentFactory.create(page=other, node=tourer)
    return {"mine": mine, "other": other, "general": general}


def _garage(client: Client, model: str) -> None:
    client.post(reverse("garage_save"), {"make": "Acme", "model": model, "year": "", "next": "/"})


def _list(client: Client, query: str = "") -> str:
    response = client.get(str(primary_index().url) + query)
    assert response.status_code == 200
    return response.content.decode()


def test_the_checkbox_needs_an_active_garage(events: dict[str, EventPage]) -> None:
    assert CHECKBOX not in _list(Client())
    client = Client()
    _garage(client, "Roadster")
    assert CHECKBOX in _list(client)


def test_the_checkbox_is_off_by_default_and_the_list_shows_every_event(
    events: dict[str, EventPage],
) -> None:
    client = Client()
    _garage(client, "Roadster")
    html = _list(client)
    assert f'{CHECKBOX} name="garage" value="1" checked' not in html
    for title in ("Roadster meet", "Tourer meet", "Open meet"):
        assert title in html


def test_the_checkbox_keeps_only_the_events_for_the_garage(events: dict[str, EventPage]) -> None:
    client = Client()
    _garage(client, "Roadster")
    html = _list(client, "?garage=1")
    assert "Roadster meet" in html
    assert "Tourer meet" not in html
    assert "Open meet" not in html
    assert 'name="garage" value="1" checked' in html
    assert "noindex" in html  # a personalised view is never indexed


def test_the_parameter_does_nothing_without_a_garage(events: dict[str, EventPage]) -> None:
    html = _list(Client(), "?garage=1")
    for title in ("Roadster meet", "Tourer meet", "Open meet"):
        assert title in html


@pytest.mark.parametrize("name", ["feed.rss", "calendar.ics"])
def test_a_feed_is_identical_with_and_without_a_garage(
    events: dict[str, EventPage], name: str
) -> None:
    url = str(primary_index().url) + name
    plain = Client().get(url)
    assert plain.status_code == 200
    client = Client()
    _garage(client, "Roadster")
    with_garage = client.get(url)
    filtered = client.get(url + "?garage=1")
    assert with_garage.content == plain.content
    assert filtered.content == plain.content
    for title in ("Roadster meet", "Tourer meet", "Open meet"):
        assert title.encode() in plain.content


@pytest.mark.parametrize("path", ["past/", "calendar/2027-05/"])
def test_the_garage_parameter_is_ignored_outside_the_list(
    events: dict[str, EventPage], path: str
) -> None:
    client = Client()
    _garage(client, "Roadster")
    base = str(primary_index().url) + path

    def body(url: str) -> str:
        # The language form differs per response (CSRF token, the request's own query).
        html = client.get(url).content.decode()
        html = re.sub(r'name="csrfmiddlewaretoken" value="[^"]*"', "", html)
        return html.replace("?garage=1", "")

    assert body(base + "?garage=1") == body(base)


def test_a_garage_page_is_private_to_the_cache(events: dict[str, EventPage]) -> None:
    client = Client()
    _garage(client, "Roadster")
    assert "private" in client.get(str(primary_index().url) + "?garage=1").get("Cache-Control", "")
    assert "private" not in Client().get(str(primary_index().url)).get("Cache-Control", "")


def test_the_checkbox_label_is_translated(events: dict[str, EventPage]) -> None:
    client = Client()
    _garage(client, "Roadster")
    with translation.override(primary_language()):
        label = translation.gettext("Only events for my garage")
    assert label in _list(client)
