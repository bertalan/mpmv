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
from apps.cms.seed import LEGAL_PLACEHOLDER_BODY, legal_placeholder
from apps.cms.services import seed_site, set_legal_effective_date
from apps.core.audit import AuditContext
from apps.core.i18n import configured_languages, primary_language
from apps.core.models import AuditLog
from tests.e2e.test_cms_pages import RECORD_CSP_VIOLATIONS, csp_violations
from tests.i18n import language_prefix, other_language, translated

pytestmark = pytest.mark.django_db(transaction=True)

CTX = AuditContext(actor_label="command:e2e", source=AuditLog.Source.COMMAND)


def test_every_language_has_the_five_pages_each_with_its_own_date(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    seed_site(legal_placeholders=True)
    today = timezone.localdate()
    primary, other = primary_language(), other_language()
    english_date = today + dt.timedelta(days=200)
    italian_date = today + dt.timedelta(days=45)
    # A new primary-language version, and the translator gives the other text its own date.
    english = LegalPage.objects.live().get(kind="terms", locale__language_code=primary)
    english.effective_date = english_date
    english.save_revision().publish()
    italian = LegalPage.objects.live().get(kind="terms", locale__language_code=other)
    set_legal_effective_date(italian, italian_date, ctx=CTX)
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    for code in configured_languages():
        for kind in GO_LIVE_LEGAL_KINDS:
            legal = LegalPage.objects.live().get(kind=kind, locale__language_code=code)
            response = page.goto(f"{live_server.url}{legal.url}")
            assert response is not None
            assert response.status == 200, (kind, code)

    page.goto(f"{live_server.url}{english.url}")
    expect(page.get_by_role("heading", level=1)).to_have_text(
        legal_placeholder(primary, "terms").title
    )
    draft = "Draft: this text has not been checked by legal counsel yet and is not final."
    expect(page.get_by_text(translated(draft))).to_be_visible()
    shot("primary-terms-placeholder")

    terms = legal_placeholder(other, "terms")
    page.locator(f'button[value="{other}"]').click()
    expect(page).to_have_url(re.compile(rf"{language_prefix(other)}/{terms.slug}/$"))
    expect(page.get_by_role("heading", level=1)).to_have_text(terms.title)
    expect(page.locator(f'time[datetime="{italian_date.isoformat()}"]').first).to_be_visible()
    expect(page.locator(f'time[datetime="{english_date.isoformat()}"]')).to_have_count(0)
    shot("other-terms-own-date")

    assert csp_violations(page) == [], "\n".join(csp_violations(page))


def test_a_missing_language_is_not_found_rather_than_english(
    page: Page, live_server: LiveServer
) -> None:
    seed_site(legal_placeholders=True)
    other = other_language()
    german = LegalPage.objects.live().get(kind="returns", locale__language_code=other)
    german_url = german.url
    assert german_url.startswith(f"{language_prefix(other)}/")
    # The primary language's placeholder sentence, without its markup.
    english_text = (
        re.sub("<[^>]+>", "", LEGAL_PLACEHOLDER_BODY[primary_language()]).split(".")[1].strip()
    )
    german.unpublish()

    response = page.goto(f"{live_server.url}{german_url}")
    assert response is not None
    assert response.status == 404
    assert english_text not in page.content()
