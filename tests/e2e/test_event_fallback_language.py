"""E2E (EVT.8): an Italian events list with a German original-language fallback item passes axe
(WCAG 2.2 AA, SC 3.1.2 language of parts) at 360, 768 and 1280 px, with the CSP intact, and the
"Only events in <language>" filter is a plain form submission."""

import datetime as dt
import re
from collections.abc import Callable
from pathlib import Path

import pytest
from django.utils import timezone
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer
from wagtail.models import Locale

from apps.accounts.tests.factories import UserFactory
from apps.cms.services import seed_site
from apps.events.models import EventIndexPage
from apps.events.services import add_event_translation, submit_event
from apps.events.tests.factories import EventPageFactory
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)

WIDTHS = (360, 768, 1280)


def _german_and_italian_events() -> None:
    """Two German events; one has an Italian translation, the other falls back to German."""
    seed_site()
    staff = UserFactory.create(is_staff=True, is_superuser=True)
    start = (timezone.now() + dt.timedelta(days=4)).replace(minute=0, second=0, microsecond=0)
    german_index = EventIndexPage.objects.get(locale__language_code="de")
    pages = []
    for title, offset in (("Herbstmesse", 0), ("Fruehlingsmesse", 3)):
        page = EventPageFactory.create(
            parent=german_index,
            title=title,
            starts_at=start + dt.timedelta(days=offset),
            ends_at=start + dt.timedelta(days=offset, hours=8),
            located=True,
        )
        page.save_revision().publish()
        pages.append(page)
    translated = add_event_translation(
        staff,
        pages[1],
        Locale.objects.get(language_code="it"),
        {"title": "Fiera di primavera", "summary": "Incontra gli artigiani."},
    ).page
    assert translated is not None
    submit_event(staff, translated)


def test_the_italian_list_with_a_german_fallback_passes_axe_and_csp(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    _german_and_italian_events()
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    for width in WIDTHS:
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(f"{live_server.url}/it/events/")
        expect(page.get_by_role("heading", level=1)).to_be_visible()
        fallback = page.locator("article.event-summary[lang='de']").first
        expect(fallback).to_be_visible()
        expect(fallback.locator(".event-language")).to_have_text("Deutsch")
        shot(f"event-fallback-it-{width}")
        found = run_axe(page)
        assert found == [], f"IT list at {width}px: {format_violations(found)}"
        assert csp_violations(page) == []


def test_the_only_translated_filter_is_a_plain_form_submission(
    page: Page, live_server: LiveServer
) -> None:
    _german_and_italian_events()
    page.goto(f"{live_server.url}/it/events/")
    expect(page.locator("article.event-summary[lang='de']").first).to_be_visible()
    expect(page.locator("article.event-summary")).to_have_count(2)
    page.locator("#event-filter-translated").check()
    page.locator("form.event-filters button[type=submit]").click()
    expect(page).to_have_url(re.compile(r"translated=1"))
    expect(page.locator("article.event-summary")).to_have_count(1)
    expect(page.locator("article.event-summary[lang]")).to_have_count(0)
