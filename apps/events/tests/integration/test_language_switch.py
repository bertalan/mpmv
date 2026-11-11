"""I18N.11: the language switch on an event page leads to the event's translation, from the same
`{language: URL}` map as its `hreflang` links; a language without one leads to the home with a
notice."""

import datetime as dt
import re

import pytest
from django.test import Client
from wagtail.models import Locale

from apps.cms.services import seed_site, translate_page
from apps.events.models import EventIndexPage, EventPage
from apps.events.tests.factories import EventPageFactory

pytestmark = pytest.mark.django_db

FORM = re.compile(
    r'<input type="hidden" name="exact" value="(?P<exact>[01])">\s*'
    r'<input type="hidden" name="notice" value="(?P<notice>[01])">\s*'
    r'<input type="hidden" name="next" value="(?P<next>[^"]*)">\s*'
    r'<button type="submit" name="language" value="(?P<code>[a-z-]+)"'
)


def targets(html: str) -> dict[str, tuple[str, bool]]:
    return {m["code"]: (m["next"], m["notice"] == "1") for m in FORM.finditer(html)}


def hreflang(html: str) -> dict[str, str]:
    return dict(re.findall(r'<link rel="alternate" hreflang="([^"]+)" href="([^"]+)"', html))


@pytest.fixture(autouse=True)
def _commit_hooks_run_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "django.db.transaction.on_commit", lambda func, using=None, robust=False: func()
    )


def _italian_event(slug: str) -> EventPage:
    seed_site()
    index = EventIndexPage.objects.get(locale__language_code="it")
    page: EventPage = EventPageFactory.create(parent=index, title=slug.title(), slug=slug)
    page.save_revision().publish()
    page.refresh_from_db()
    return page


def test_an_event_with_a_translation_switches_to_it_and_matches_hreflang(client: Client) -> None:
    page = _italian_event("fiera")
    translate_page(page, ["en"])
    english = page.get_translation(Locale.objects.get(language_code="en"))
    html = client.get(page.url).content.decode()
    found = targets(html)
    assert found["en"] == (english.url, False)
    assert found["de"] == ("/de/", True)
    alternates = {code: url for code, url in hreflang(html).items() if code != "x-default"}
    assert set(alternates) == {"it", "en"}
    for code, url in alternates.items():
        assert url.endswith(found[code][0])
    assert {code for code, (_u, notice) in found.items() if not notice} == set(alternates)


def test_an_event_without_a_translation_has_no_alternate_and_a_notice(client: Client) -> None:
    page = _italian_event("solo-italiano")
    html = client.get(page.url).content.decode()
    found = targets(html)
    assert found["en"] == ("/", True)
    assert found["de"] == ("/de/", True)
    assert {code for code in hreflang(html) if code != "x-default"} == {"it"}


def test_a_hidden_translation_is_not_offered(client: Client) -> None:
    page = _italian_event("nascosto")
    translate_page(page, ["en"])
    english = page.get_translation(Locale.objects.get(language_code="en")).specific
    EventPage.objects.filter(pk=english.pk).update(hidden_at=dt.datetime.now(dt.UTC))
    found = targets(client.get(page.url).content.decode())
    assert found["en"][1] is True
