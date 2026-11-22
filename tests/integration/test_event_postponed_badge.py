"""EVT.10 2/2: the postponed badge and the tickets line in both themes (base and ride).

Here, not in apps/events/tests: switching the theme needs apps.themes, which the events app
must not import (import contracts, "No app imports themes").
"""

import datetime as dt
from decimal import Decimal

import pytest
from django.test import Client
from wagtail.models import Locale

from apps.cms.services import seed_site
from apps.events.models import EventIndexPage, EventPage
from apps.events.tests.factories import EventPageFactory
from apps.themes.services import use_theme
from tests.i18n import other_language

pytestmark = pytest.mark.django_db


def _publish(page: EventPage) -> EventPage:
    page.save_revision().publish()
    page.refresh_from_db()
    return page


def _html(client: Client, url: str) -> str:
    response = client.get(url)
    assert response.status_code == 200, url
    return response.content.decode()


@pytest.mark.parametrize("theme", ["base", "ride"])
@pytest.mark.parametrize("which", ["primary", "other"])
def test_a_postponed_event_shows_a_badge_on_the_page_and_in_the_list(
    client: Client, theme: str, which: str
) -> None:
    from django.utils import timezone, translation

    from apps.core.i18n import primary_language

    seed_site()
    code = primary_language() if which == "primary" else other_language()
    index = EventIndexPage.objects.get(locale=Locale.objects.get(language_code=code))
    start = timezone.now() + dt.timedelta(days=10)
    page = _publish(
        EventPageFactory.create(
            parent=index,
            starts_at=start,
            ends_at=start + dt.timedelta(hours=2),
            postponed=True,
            previous_start=start - dt.timedelta(days=30),
            price=Decimal("5"),
            ticket_url="https://tickets.example/fair",
        )
    )
    with translation.override(code):
        word = str(translation.gettext("Postponed"))
        tickets = str(translation.gettext("Get tickets"))
    with use_theme(theme):
        detail = _html(client, page.url)
        listing = _html(client, index.url)
    assert f'<p role="status" class="event-status">{word}</p>' in detail
    assert f'<span class="event-status">{word}</span>' in listing
    assert tickets in detail
    assert 'href="https://tickets.example/fair"' in detail
    assert "Cancelled" not in detail
