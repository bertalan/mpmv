"""EVT.1 part 2/2: minimal public templates of `EventIndexPage` and `EventPage` (spec 15
"Public pages") on the base theme layouts.

Semantic markup (`<article>`, `<time datetime>`, `<address>`), no inline script or style
(CSP, spec 14 §14.5), dates in the page's language and the event's time zone, the type as text.
The full listing rules (filters, months, past archive, language fallback) come with EVT.6 and
the map with EVT.15.
"""

import datetime as dt
import re
from decimal import Decimal

import pytest
from django.test import Client
from wagtail.models import Locale

from apps.cms.services import seed_site
from apps.events.models import EventIndexPage, EventPage
from apps.events.tests.factories import EventPageFactory, primary_index
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


def _assert_csp_safe(html: str) -> None:
    assert "<style" not in html
    assert " style=" not in html
    for script in re.findall(r"<script[^>]*>", html):
        assert "src=" in script or 'type="application/ld+json"' in script, script


def test_the_event_page_shows_title_dates_type_vendor_and_address(client: Client) -> None:
    page = _publish(
        EventPageFactory.create(
            located=True,
            title="Spring fair",
            venue_name="Exhibition centre",
            booth="B12",
        )
    )
    html = _html(client, page.url)
    assert "<h1>Spring fair</h1>" in html
    assert "layouts" not in html  # rendered, not a missing template
    # 08:00 UTC is 09:00 in Rome; the machine-readable value keeps the offset.
    assert '<time datetime="2027-03-10T09:00:00+01:00"' in html
    assert "Europe/Rome" in html
    assert "Trade fair or meeting" in html
    assert page.vendor is not None
    assert page.vendor.display_name in html
    assert "<address" in html
    for text in ("Exhibition centre", "Via Esempio 1", "20121", "Milano", "B12"):
        assert text in html, text
    assert "Come and see us." in html
    _assert_csp_safe(html)


def test_an_all_day_event_shows_days_without_times(client: Client) -> None:
    page = _publish(EventPageFactory.create(all_day_event=True))
    html = _html(client, page.url)
    assert '<time datetime="2027-08-28">' in html
    assert '<time datetime="2027-09-02">' in html


def test_an_online_event_shows_its_link_and_no_address(client: Client) -> None:
    page = _publish(EventPageFactory.create(online=True))
    html = _html(client, page.url)
    assert 'href="https://meet.example.com/fair"' in html
    assert "<address" not in html


def test_a_cancelled_event_says_so(client: Client) -> None:
    page = EventPageFactory.create(cancelled_at=dt.datetime(2027, 2, 1, tzinfo=dt.UTC))
    html = _html(client, _publish(page).url)
    assert 'role="status"' in html
    assert "Cancelled" in html


def test_dates_are_written_in_the_page_language(client: Client) -> None:
    seed_site()
    italian = EventIndexPage.objects.get(locale=Locale.objects.get(language_code=other_language()))
    page = _publish(EventPageFactory.create(parent=italian, title="Fiera di primavera"))
    html = _html(client, page.url)
    assert f'<html lang="{other_language()}"' in html
    assert "marzo" in html.lower()  # 10 March 2027, in Italian
    assert "Fiera di primavera" in html


def test_the_index_lists_live_events_of_its_language_in_date_order(client: Client) -> None:
    index = primary_index()
    later = _publish(
        EventPageFactory.create(
            title="Autumn fair",
            starts_at=dt.datetime(2027, 10, 1, 8, 0, tzinfo=dt.UTC),
        )
    )
    sooner = _publish(EventPageFactory.create(title="Spring fair"))
    draft = EventPageFactory.create(title="Secret draft")
    html = _html(client, index.url)
    assert "<h1>" in html
    assert html.count("<article") == 2
    assert html.index(sooner.title) < html.index(later.title)
    assert draft.title not in html
    assert f'href="{sooner.url}"' in html
    _assert_csp_safe(html)


def test_an_empty_index_says_there_are_no_events(client: Client) -> None:
    html = _html(client, primary_index().url)
    assert "<article" not in html
    assert "No events" in html


@pytest.mark.parametrize("theme", ["base", "ride"])
@pytest.mark.parametrize("which", ["primary", "other"])
def test_a_postponed_event_shows_a_badge_on_the_page_and_in_the_list(
    client: Client, theme: str, which: str
) -> None:
    from django.utils import timezone, translation

    from apps.core.i18n import primary_language
    from apps.themes.services import use_theme

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


def test_cancelled_wins_over_the_postponed_badge(client: Client) -> None:
    page = EventPageFactory.create(
        postponed=True, cancelled_at=dt.datetime(2027, 2, 1, tzinfo=dt.UTC)
    )
    html = _html(client, _publish(page).url)
    assert "Cancelled" in html
    assert "Postponed" not in html


def test_an_event_that_is_not_postponed_has_no_badge_or_tickets_line(client: Client) -> None:
    html = _html(client, _publish(EventPageFactory.create()).url)
    assert "Postponed" not in html
    assert "Get tickets" not in html


def test_ticket_price_is_localised_and_the_link_is_noreferrer(client: Client) -> None:
    from django.utils import translation

    from apps.core.i18n import primary_language
    from apps.core.money import CURRENCY

    seed_site()
    for code in (primary_language(), other_language()):
        index = EventIndexPage.objects.get(locale=Locale.objects.get(language_code=code))
        page = _publish(
            EventPageFactory.create(
                parent=index, price=Decimal("1234.5"), ticket_url="https://tickets.example/x"
            )
        )
        html = _html(client, page.url)
        with translation.override(code):
            from django.utils.formats import number_format

            shown = number_format(Decimal("1234.50"), 2, use_l10n=True)
        assert f'<data value="1234.50">{CURRENCY} {shown}</data>' in html
        assert 'rel="noopener noreferrer"' in html
