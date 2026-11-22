"""E2E (I18N.3): a translator replaces one image in one translation only, in wagtail-localize.

Done when: "Translated page with sync; a translator replaces one image in one locale only".
The page and its translations are set up through the services; the translator then works in
the browser on a second language's copy: translation editor, image chooser, "Publish in ...".
"""

import re
import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.accounts.tests.factories import TOTPDeviceFactory, UserFactory
from apps.accounts.tests.mfa import totp_token
from apps.cms.models import StandardPage
from apps.cms.services import translate_page
from apps.cms.tests.factories import StandardPageFactory
from apps.core.i18n import configured_languages, primary_language
from apps.core.tests.factories import MediaImageFactory
from tests.e2e.login import verify_with_totp
from tests.e2e.test_cms_home_blocks import translated_js
from tests.e2e.test_cms_pages import RECORD_CSP_VIOLATIONS, csp_violations
from tests.i18n import language_prefix, other_language, translated

pytestmark = pytest.mark.django_db(transaction=True)


def _axe_helper_style(violation: str) -> bool:
    """Known since CMS.1: Wagtail's accessibility checker adds a <style> the CSP blocks."""
    return "style-src-elem blocked inline" in violation and "wagtailadmin/js/vendor.js" in violation


def test_a_translator_replaces_the_main_image_of_one_translation_only(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    password = secrets.token_urlsafe(16)  # generated: no credential literals in git
    translator = UserFactory.create(
        email="translator@example.com", password=password, superuser=True
    )
    device = TOTPDeviceFactory.create(user=translator)
    english = MediaImageFactory.create(title="Workshop EN", file__filename="workshop-en.png")
    MediaImageFactory.create(title="Workshop IT", file__filename="workshop-it.png")
    source = StandardPageFactory.create(
        title="Workshop",
        slug="workshop",
        image=english,
        image_alt_text="A workbench with tools",
        live=False,
    )
    source.save_revision().publish()
    other = other_language()
    rest = [code for code in configured_languages() if code not in (primary_language(), other)]
    translate_page(source, [other, *rest], user=translator)
    translation = StandardPage.objects.get(
        translation_key=source.translation_key, locale__language_code=other
    )
    page.add_init_script(RECORD_CSP_VIOLATIONS)

    page.goto(f"{live_server.url}/admin/login/")
    page.locator("input[name=username]").fill("translator@example.com")
    page.locator("input[name=password]").fill(password)
    page.get_by_role("button", name=translated("Sign in")).click()
    verify_with_totp(page, totp_token(device))

    page.goto(f"{live_server.url}/admin/pages/{translation.pk}/edit/")
    expect(page.get_by_text("A workbench with tools").first).to_be_visible()
    expect(page.locator(".preview-image img").first).to_have_attribute("title", "Workshop EN")
    shot("translation-editor")

    page.get_by_role("button", name=translated("Change image")).first.click()
    modal = page.locator(".modal")
    expect(modal.get_by_text("Workshop IT")).to_be_visible()
    shot("image-chooser")
    modal.get_by_text("Workshop IT").click()
    expect(modal).to_be_hidden()
    expect(page.locator(".preview-image img").first).to_have_attribute("title", "Workshop IT")
    shot("image-replaced")

    page.get_by_role(
        "button", name=re.compile("^" + re.escape(translated_js("Publish in ")))
    ).click()
    expect(page.locator(".messages")).to_be_visible()

    page.goto(f"{live_server.url}{language_prefix(other)}/workshop/")
    expect(page.locator("article img").first).to_have_attribute("src", re.compile("workshop-it"))
    shot("translated-page")
    page.goto(f"{live_server.url}/workshop/")
    expect(page.locator("article img").first).to_have_attribute("src", re.compile("workshop-en"))
    for code in rest:  # the other translations keep the source image
        page.goto(f"{live_server.url}{language_prefix(code)}/workshop/")
        expect(page.locator("article img").first).to_have_attribute(
            "src", re.compile("workshop-en")
        )
        shot(f"{code}-page")

    others = [v for v in csp_violations(page) if not _axe_helper_style(v)]
    assert others == [], "\n".join(others)
