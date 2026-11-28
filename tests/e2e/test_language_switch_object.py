"""E2E (I18N.11): the language switch stays on the same object.

On a taxonomy term page the switch to a second language leads to the term page in it (with the
filter written in it), not to its home; a language without a translation stays on the page, shown
in the primary language with the fallback notice (I18N.22). Real browser, no CSP violation.
"""

import html
import re
from collections.abc import Callable
from pathlib import Path

import pytest
from django.utils import translation
from django.utils.translation import gettext
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.catalog.taxonomy import TaxonomyTranslation, TermTranslation
from apps.core.i18n import primary_language
from tests.e2e.test_catalog_listings import term_page  # noqa: F401
from tests.e2e.test_cms_pages import RECORD_CSP_VIOLATIONS, csp_violations
from tests.i18n import fallback_notice, language_prefix, other_language

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.usefixtures("term_page")
def test_the_switch_stays_on_the_same_term_page(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    second = other_language()
    page.goto(f"{live_server.url}/compatibility/maker/?colour=red")
    switcher = page.locator("nav.language-switcher")
    shot("switch-primary")
    switcher.locator(f'button[value="{second}"]').click()
    expect(page).to_have_url(
        f"{live_server.url}{language_prefix(second)}/compatibilita/marca/?colore=rosso"
    )
    expect(page.get_by_role("heading", level=1)).to_have_text("Marca")
    expect(page.locator(".site-messages")).to_have_count(0)
    shot("switch-second")
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


@pytest.mark.usefixtures("term_page")
def test_a_language_without_the_term_stays_on_the_page_with_the_fallback_notice(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    second = other_language()
    # The term and its taxonomy lose their second-language translation.
    TermTranslation.objects.filter(term__taxonomy__code="compat", language=second).delete()
    TaxonomyTranslation.objects.filter(taxonomy__code="compat", language=second).delete()
    page.goto(f"{live_server.url}/compatibility/maker/?colour=red")
    page.locator("nav.language-switcher").locator(f'button[value="{second}"]').click()
    # I18N.22: the same term page under the new prefix (never the home), in the primary language.
    expect(page).to_have_url(
        re.compile(rf"^{re.escape(live_server.url + language_prefix(second))}/[^/?]+/.*colo?u?r=")
    )
    expect(page.locator(".site-messages")).to_contain_text(
        html.unescape(fallback_notice(primary_language(), second))
    )
    shot("switch-second-notice")
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


def switch_notice(language: str) -> str:
    """The switch notice as the page shows it in `language` (the catalogs translate it)."""
    with translation.override(language):
        return gettext("This content is not available in this language.")
