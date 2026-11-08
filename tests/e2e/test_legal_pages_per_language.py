"""E2E (I18N.9): customer legal and checkout pages in every go-live language (Q-42).

Done when: "Every one of the five pages is published in all five languages; ... a translation of
a legal page shows its own effective date". The seed's placeholders are marked as drafts until
legal counsel has checked them; a language without the page gets no EN fallback.
"""

import datetime as dt
import re
from collections.abc import Callable
from pathlib import Path

import pytest
from django.utils import timezone
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.cms.engine import GO_LIVE_LEGAL_KINDS
from apps.cms.models import LegalPage
from apps.cms.services import seed_site, set_legal_effective_date
from apps.core.audit import AuditContext
from apps.core.i18n import configured_languages
from apps.core.models import AuditLog
from tests.e2e.test_cms_pages import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)

CTX = AuditContext(actor_label="command:e2e", source=AuditLog.Source.COMMAND)


def test_every_language_has_the_five_pages_each_with_its_own_date(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    seed_site(legal_placeholders=True)
    today = timezone.localdate()
    english_date = today + dt.timedelta(days=200)
    italian_date = today + dt.timedelta(days=45)
    # A new English version, and the translator gives the Italian text its own date.
    english = LegalPage.objects.live().get(kind="terms", locale__language_code="en")
    english.effective_date = english_date
    english.save_revision().publish()
    italian = LegalPage.objects.live().get(kind="terms", locale__language_code="it")
    set_legal_effective_date(italian, italian_date, ctx=CTX)
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    for code in configured_languages():
        for kind in GO_LIVE_LEGAL_KINDS:
            legal = LegalPage.objects.live().get(kind=kind, locale__language_code=code)
            response = page.goto(f"{live_server.url}{legal.url}")
            assert response is not None
            assert response.status == 200, (kind, code)

    page.goto(f"{live_server.url}{english.url}")
    expect(page.get_by_role("heading", level=1)).to_have_text("Terms of sale")
    expect(page.get_by_text(re.compile("not been checked by legal counsel"))).to_be_visible()
    shot("en-terms-placeholder")

    page.locator('button[value="it"]').click()
    expect(page).to_have_url(re.compile(r"/it/condizioni-di-vendita/$"))
    expect(page.get_by_role("heading", level=1)).to_have_text("Condizioni di vendita")
    expect(page.locator(f'time[datetime="{italian_date.isoformat()}"]').first).to_be_visible()
    expect(page.locator(f'time[datetime="{english_date.isoformat()}"]')).to_have_count(0)
    shot("it-terms-own-date")

    assert csp_violations(page) == [], "\n".join(csp_violations(page))


def test_a_missing_language_is_not_found_rather_than_english(
    page: Page, live_server: LiveServer
) -> None:
    seed_site(legal_placeholders=True)
    german = LegalPage.objects.live().get(kind="returns", locale__language_code="de")
    german_url = german.url
    assert german_url.startswith("/de/")
    english_text = "This page has no legal text yet"  # the EN placeholder's sentence
    german.unpublish()

    response = page.goto(f"{live_server.url}{german_url}")
    assert response is not None
    assert response.status == 404
    assert english_text not in page.content()
