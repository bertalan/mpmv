"""EVT.1 done-when: an IT page translates to EN with dates synchronised (spec 15 "Languages").

Owner decision Q-72: an event may be written in any enabled language, with no EN requirement.
The IT original is translated with wagtail-localize (the CMS translation services, I18N.3);
the structured fields (dates, zone, location, vendor, original language) are copied, the texts
are the translator's, and a later date change on the original reaches the translation.
"""

import datetime as dt
from decimal import Decimal

import pytest
from wagtail.models import Locale

from apps.cms.services import seed_site, sync_page_translations, translate_page
from apps.events.models import EventIndexPage, EventPage
from apps.events.tests.factories import EventPageFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _commit_hooks_run_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """wagtail-localize publishes a translation when the transaction commits; a test runs in
    one transaction that never commits, so the hooks run at once here."""
    monkeypatch.setattr(
        "django.db.transaction.on_commit", lambda func, using=None, robust=False: func()
    )


@pytest.fixture
def italian_event() -> EventPage:
    """A published event written in Italian only: no EN page exists."""
    seed_site()
    index = EventIndexPage.objects.get(locale__language_code="it")
    page: EventPage = EventPageFactory.create(
        parent=index,
        title="Fiera di primavera",
        slug="fiera-di-primavera",
        summary="Incontra gli artigiani alla fiera.",
        venue_name="Padiglione 3",
        located=True,
    )
    page.save_revision().publish()
    page.refresh_from_db()
    return page


def test_an_event_needs_no_english_page(italian_event: EventPage) -> None:
    assert italian_event.original_locale.language_code == "it"
    assert italian_event.get_translation_or_none(Locale.objects.get(language_code="en")) is None
    assert italian_event.status == EventPage.Status.PUBLISHED


def test_an_italian_page_translates_to_english_with_synchronised_dates(
    italian_event: EventPage,
) -> None:
    translate_page(italian_event, ["en"])
    english = italian_event.get_translation(Locale.objects.get(language_code="en")).specific

    assert english.get_parent().specific == EventIndexPage.objects.get(locale__language_code="en")
    for field in (
        "starts_at",
        "ends_at",
        "timezone",
        "event_type",
        "attendance",
        "vendor_id",
        "city",
        "country",
        "latitude",
        "longitude",
        "geo_source",
    ):
        assert getattr(english, field) == getattr(italian_event, field), field
    assert english.original_locale.language_code == "it"
    assert english.translation_key == italian_event.translation_key

    new_start = italian_event.starts_at + dt.timedelta(days=7)
    italian_event.starts_at = new_start
    italian_event.ends_at = new_start + dt.timedelta(hours=6)
    italian_event.latitude = Decimal("45.500000")
    italian_event.save_revision().publish()
    sync_page_translations(italian_event)

    english.refresh_from_db()
    assert english.starts_at == new_start
    assert english.ends_at == new_start + dt.timedelta(hours=6)
    assert english.latitude == Decimal("45.500000")
