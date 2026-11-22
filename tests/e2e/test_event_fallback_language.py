"""E2E (EVT.8): the primary language's events list with an original-language fallback item (a
second language) passes axe (WCAG 2.2 AA, SC 3.1.2 language of parts) at 360, 768 and 1280 px,
with the CSP intact, and the "Only events in <language>" filter is a plain form submission."""

import datetime as dt
import re
from collections.abc import Callable
from pathlib import Path

import pytest
from django.utils import timezone, translation
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer
from wagtail.models import Locale

from apps.accounts.tests.factories import UserFactory
from apps.cms.services import seed_site
from apps.core.i18n import primary_language
from apps.events.models import EventIndexPage
from apps.events.services import add_event_translation, submit_event
from apps.events.tests.factories import EventPageFactory
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations
from tests.i18n import other_language

pytestmark = pytest.mark.django_db(transaction=True)

WIDTHS = (360, 768, 1280)


def _events_in_another_language() -> str:
    """Two events in a second language; one has a translation into the primary language, the
    other falls back to its original. Returns the primary language's events list path."""
    seed_site()
    staff = UserFactory.create(is_staff=True, is_superuser=True)
    start = (timezone.now() + dt.timedelta(days=4)).replace(minute=0, second=0, microsecond=0)
    german_index = EventIndexPage.objects.get(locale__language_code=other_language())
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
        Locale.objects.get(language_code=primary_language()),
        {"title": "Fiera di primavera", "summary": "Incontra gli artigiani."},
    ).page
    assert translated is not None
    submit_event(staff, translated)
    return str(EventIndexPage.objects.get(locale__language_code=primary_language()).url)


def test_the_list_with_an_original_language_fallback_passes_axe_and_csp(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    index = _events_in_another_language()
    other = other_language()
    name = str(translation.get_language_info(other)["name_local"])
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    for width in WIDTHS:
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(f"{live_server.url}{index}")
        expect(page.get_by_role("heading", level=1)).to_be_visible()
        fallback = page.locator(f"article.event-summary[lang='{other}']").first
        expect(fallback).to_be_visible()
        expect(fallback.locator(".event-language")).to_have_text(name)
        shot(f"event-fallback-{width}")
        found = run_axe(page)
        assert found == [], f"list at {width}px: {format_violations(found)}"
        assert csp_violations(page) == []


def test_the_only_translated_filter_is_a_plain_form_submission(
    page: Page, live_server: LiveServer
) -> None:
    index = _events_in_another_language()
    page.goto(f"{live_server.url}{index}")
    expect(page.locator(f"article.event-summary[lang='{other_language()}']").first).to_be_visible()
    expect(page.locator("article.event-summary")).to_have_count(2)
    page.locator("#event-filter-translated").check()
    page.locator("form.event-filters button[type=submit]").click()
    expect(page).to_have_url(re.compile(r"translated=1"))
    expect(page.locator("article.event-summary")).to_have_count(1)
    expect(page.locator("article.event-summary[lang]")).to_have_count(0)
