"""E2E (I18N.11): the language switch stays on the same object.

On a taxonomy term page the switch to Italian leads to the Italian term page (with the filter
written in Italian), not to the Italian home; a language without a translation leads to its home
with a notice. Works in a real browser, no CSP violation.
"""

from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from tests.e2e.test_catalog_listings import term_page  # noqa: F401
from tests.e2e.test_cms_pages import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.usefixtures("term_page")
def test_the_switch_stays_on_the_same_term_page(
    page: Page, live_server: LiveServer, shot: Callable[[str], Path]
) -> None:
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.goto(f"{live_server.url}/compatibility/maker/?colour=red")
    switcher = page.locator("nav.language-switcher")
    shot("switch-en")
    switcher.locator('button[value="it"]').click()
    expect(page).to_have_url(f"{live_server.url}/it/compatibilita/marca/?colore=rosso")
    expect(page.get_by_role("heading", level=1)).to_have_text("Marca")
    expect(page.locator(".site-messages")).to_have_count(0)
    shot("switch-it")

    switcher.locator('button[value="de"]').click()
    expect(page).to_have_url(f"{live_server.url}/de/")
    expect(page.locator(".site-messages")).to_contain_text("not available in this language")
    shot("switch-de-notice")
    assert csp_violations(page) == [], "\n".join(csp_violations(page))
