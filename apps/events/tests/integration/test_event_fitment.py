"""FIT.11 (1/2): `fitment_nodes` on an event and the "For <profile> owners" badge (spec 17 §17.5.5).

A tagged event shows the badge on the list and on its page when the active garage matches (the
event's node is the garage's model or one of its ancestors); an event without nodes is general and
never gets one; a visitor without a garage sees no badge; the page URL and the garage never reach
the markup.
"""

import datetime as dt

import pytest
from django.apps import apps
from django.db import connection
from django.db.models import ProtectedError
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone, translation

from apps.core.i18n import primary_language
from apps.events.models import EventFitment, EventPage
from apps.events.tests.factories import EventFitmentFactory, EventPageFactory, primary_index
from apps.fitment.tests.factories import FitmentNodeFactory

pytestmark = pytest.mark.django_db

BADGE = 'class="event-fitment"'


@pytest.fixture(autouse=True)
def _indexing_on() -> None:
    from wagtail.models import Site

    apps.get_model("seo", "SeoSettings").objects.create(
        site=Site.objects.get(is_default_site=True), allow_indexing=True
    )


@pytest.fixture
def tree() -> dict[str, object]:
    make = FitmentNodeFactory.create(name="Acme")
    roadster = FitmentNodeFactory.create(name="Roadster", parent=make)
    tourer = FitmentNodeFactory.create(name="Tourer", parent=make)
    return {"make": make, "roadster": roadster, "tourer": tourer}


def _event(days: int = 10, **fields: object) -> EventPage:
    start = (timezone.now() + dt.timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
    page = EventPageFactory.create(starts_at=start, ends_at=start + dt.timedelta(hours=8), **fields)
    page.save_revision().publish()
    page.refresh_from_db()
    return page


def _garage(client: Client, model: str) -> None:
    response = client.post(
        reverse("garage_save"), {"make": "Acme", "model": model, "year": "", "next": "/"}
    )
    assert response.status_code in (200, 302)


def _list(client: Client) -> str:
    response = client.get(str(primary_index().url))
    assert response.status_code == 200
    return response.content.decode()


def test_a_tagged_event_gets_the_badge_for_a_matching_garage(tree: dict[str, object]) -> None:
    tagged = _event(title="Tagged meet")
    plain = _event(days=11, title="General meet")
    EventFitmentFactory.create(page=tagged, node=tree["roadster"])
    client = Client()
    _garage(client, "Roadster")
    html = _list(client)
    cards = html.split('<article class="event-summary"')[1:]
    assert cards
    for card in cards:
        card = card.split("</article>")[0]
        assert (BADGE in card) == ("Tagged meet" in card), card
    assert any(BADGE in card for card in cards)
    assert BADGE in client.get(tagged.url).content.decode()
    assert BADGE not in client.get(plain.url).content.decode()


def test_a_make_tag_covers_every_model_of_the_make(tree: dict[str, object]) -> None:
    event = _event()
    EventFitmentFactory.create(page=event, node=tree["make"])
    client = Client()
    _garage(client, "Tourer")
    assert BADGE in _list(client)


def test_another_model_gets_no_badge(tree: dict[str, object]) -> None:
    event = _event()
    EventFitmentFactory.create(page=event, node=tree["roadster"])
    client = Client()
    _garage(client, "Tourer")
    assert BADGE not in _list(client)


def test_no_garage_no_badge_and_the_list_is_the_same(tree: dict[str, object]) -> None:
    event = _event()
    EventFitmentFactory.create(page=event, node=tree["roadster"])
    assert BADGE not in _list(Client())
    assert BADGE not in Client().get(event.url).content.decode()


def test_the_badge_never_writes_the_url_or_the_referer(tree: dict[str, object]) -> None:
    event = _event()
    EventFitmentFactory.create(page=event, node=tree["roadster"])
    client = Client()
    _garage(client, "Roadster")
    response = client.get(
        str(primary_index().url), headers={"referer": "https://elsewhere.example/leak"}
    )
    html = response.content.decode()
    assert BADGE in html
    badge = html[html.index(BADGE) :].split("</p>")[0]
    assert "href" not in badge
    assert "<form" not in badge
    assert "elsewhere.example" not in html


def test_a_translation_reads_the_nodes_of_the_original(tree: dict[str, object]) -> None:
    event = _event()
    EventFitmentFactory.create(page=event, node=tree["roadster"])
    from apps.events.fitment import nodes_of

    assert [n.pk for n in nodes_of(event)] == [tree["roadster"].pk]  # type: ignore[attr-defined]


def test_a_node_with_an_event_cannot_be_deleted_and_merge_moves_the_rows(
    tree: dict[str, object],
) -> None:
    event = _event()
    row = EventFitmentFactory.create(page=event, node=tree["roadster"])
    with pytest.raises(ProtectedError):
        tree["roadster"].delete()  # type: ignore[attr-defined]
    assert EventFitment.objects.filter(pk=row.pk).exists()
    from apps.fitment.references import reference_counts

    assert reference_counts(tree["roadster"])["events.eventfitment.node"] == 1  # type: ignore[arg-type]


def test_a_page_built_with_an_active_garage_is_private_to_the_cache(
    tree: dict[str, object],
) -> None:
    event = _event()
    EventFitmentFactory.create(page=event, node=tree["roadster"])
    anonymous = Client()
    assert "private" not in anonymous.get(event.url).get("Cache-Control", "")
    client = Client()
    _garage(client, "Roadster")
    assert "private" in client.get(event.url).get("Cache-Control", "")
    assert "private" in client.get(str(primary_index().url)).get("Cache-Control", "")


def _queries(client: Client) -> int:
    client.get(str(primary_index().url))  # warm caches
    with CaptureQueriesContext(connection) as captured:
        assert _list(client)
    return len(captured)


def test_the_badge_costs_a_constant_number_of_queries(
    tree: dict[str, object],
) -> None:
    """The extra queries a garage costs are the same for 5 and for a full page of tagged events."""
    for days in range(10, 15):
        EventFitmentFactory.create(page=_event(days, title=f"Meet {days}"), node=tree["roadster"])
    visitor = Client()
    _garage(visitor, "Roadster")
    few = _queries(visitor) - _queries(Client())
    for days in range(15, 25):
        EventFitmentFactory.create(page=_event(days, title=f"Meet {days}"), node=tree["roadster"])
    many = _queries(visitor) - _queries(Client())
    assert _list(visitor).count(BADGE) >= 12  # a full page of tagged events
    assert few == many  # a fixed cost for the garage, nothing per event


def test_the_badge_is_translated_in_the_page_language(tree: dict[str, object]) -> None:
    event = _event()
    EventFitmentFactory.create(page=event, node=tree["roadster"])
    client = Client()
    _garage(client, "Roadster")
    with translation.override(primary_language()):
        expected = translation.gettext("For %(name)s owners") % {"name": "Acme, Roadster"}
    assert expected in client.get(event.url).content.decode()
