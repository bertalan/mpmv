"""E2E (CMS.5): versioned legal pages per language and the "Sell with us" page.

Done when: "Legal page versions kept per language, so a translated legal page has its own version
and effective date (Q-42); the recruitment page shows the plans and links to the application".
"""

import datetime as dt
import re
import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer
from wagtail.models import Locale

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from apps.cms.models import HomePage, LegalPage
from apps.cms.tests.factories import LegalPageFactory, VendorRecruitmentPageFactory
from tests.e2e.login import verify_with_totp
from tests.e2e.test_cms_pages import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)


def _publish(page: LegalPage) -> LegalPage:
    page.save_revision().publish()
    page.refresh_from_db()
    return page


def _terms_in_english_and_italian() -> None:
    english = LegalPageFactory.create(
        title="Terms of sale", effective_date=dt.date(2026, 1, 1), live=False
    )
    english = _publish(english)
    english.body = [("paragraph", "<p>Updated terms of sale.</p>")]
    english.effective_date = dt.date(2026, 6, 1)
    english = _publish(english)

    italian: LegalPage = english.copy_for_translation(
        Locale.objects.get(language_code="it")
    ).specific
    italian.title = "Condizioni di vendita"
    italian.slug = "condizioni-di-vendita"
    italian.effective_date = dt.date(2026, 3, 1)
    italian.body = [("paragraph", "<p>Condizioni di vendita in italiano.</p>")]
    _publish(italian)


def test_visitor_reads_the_terms_their_archive_and_the_italian_version(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    _terms_in_english_and_italian()
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    page.goto(f"{live_server.url}/terms-of-sale/")
    expect(page.get_by_role("heading", level=1)).to_have_text("Terms of sale")
    expect(page.get_by_text("Updated terms of sale.")).to_be_visible()
    expect(page.locator('time[datetime="2026-06-01"]').first).to_be_visible()
    shot("en-terms")

    page.get_by_role("link", name=re.compile("Version 1")).click()
    expect(page).to_have_url(re.compile(r"/terms-of-sale/versions/1/$"))
    expect(page.get_by_text("Sample terms.")).to_be_visible()
    expect(page.locator('time[datetime="2026-01-01"]').first).to_be_visible()
    shot("en-terms-version-1")

    page.goto(f"{live_server.url}/terms-of-sale/")
    page.locator('button[value="it"]').click()
    expect(page).to_have_url(re.compile(r"/it/condizioni-di-vendita/$"))
    expect(page.get_by_role("heading", level=1)).to_have_text("Condizioni di vendita")
    expect(page.locator('time[datetime="2026-03-01"]').first).to_be_visible()
    shot("it-terms")

    assert csp_violations(page) == [], "\n".join(csp_violations(page))


def test_visitor_sees_the_plans_and_how_to_apply(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    VendorRecruitmentPageFactory.create(title="Sell with us")
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    page.goto(f"{live_server.url}/sell-with-us/")
    expect(page.get_by_role("heading", level=1)).to_have_text("Sell with us")
    plans = page.get_by_role("region", name="Plans")
    expect(plans.get_by_role("heading", name="Starter")).to_be_visible()
    expect(plans.get_by_role("heading", name="Workshop")).to_be_visible()
    expect(page.get_by_text("Who can sell?")).to_be_visible()
    expect(page.get_by_role("link", name="Apply now")).to_have_attribute("href", "/vendor/apply/")
    shot("sell-with-us")

    assert csp_violations(page) == [], "\n".join(csp_violations(page))


def test_editor_adds_a_legal_page_in_the_admin(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    user = UserFactory.create(email="editor@example.com", password=password, superuser=True)
    device = TOTPDeviceFactory.create(user=user)

    page.goto(f"{live_server.url}/admin/login/")
    page.locator("input[name=username]").fill("editor@example.com")
    page.locator("input[name=password]").fill(password)
    page.get_by_role("button", name="Sign in").click()
    verify_with_totp(page, totp_token(device))

    home = HomePage.objects.get(locale__language_code="en")
    page.goto(f"{live_server.url}/admin/pages/add/cms/legalpage/{home.pk}/")
    page.locator("#id_title").fill("Privacy policy")
    page.locator("#id_kind").select_option("privacy")
    page.locator("#id_effective_date").fill("2026-01-01")
    shot("admin-add-legal-page")
    page.get_by_role("button", name="More actions").click()
    page.get_by_role("button", name="Publish").click()
    expect(page.get_by_text("Page 'Privacy policy' created and published.")).to_be_visible()

    legal = LegalPage.objects.get(title="Privacy policy")
    assert [(v.number, v.effective_date) for v in legal.versions.all()] == [
        (1, dt.date(2026, 1, 1))
    ]
