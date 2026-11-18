"""E2E (CMS.17): the footer columns and social profiles of the CMS settings, in both themes.

An editor's columns and profile show in the footer at 360, 768 and 1280 px; axe `wcag22aa` is
clean and nothing raises a `securitypolicyviolation`.
"""

import pytest
from playwright.sync_api import Page, expect
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer
from wagtail.models import Site

from apps.cms.models import FooterColumn, FooterSettings, SocialProfile
from apps.core.i18n import primary_language
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("_cms_site")]


@pytest.mark.parametrize("theme", ["base", "ride"])
@pytest.mark.parametrize("width", [360, 768, 1280])
def test_the_footer_with_columns_and_profiles_passes_axe(
    theme: str,
    width: int,
    page: Page,
    live_server: LiveServer,
    settings: SettingsWrapper,
) -> None:
    settings.MPMV_THEME_DEFAULT = theme
    footer = FooterSettings.objects.create(site=Site.objects.get(is_default_site=True))
    for n in range(4):
        FooterColumn.objects.create(
            settings=footer,
            language=primary_language(),
            title=f"Column {n}",
            links=[("external", {"url": f"https://example.com/{n}/", "label": f"Link {n}"})],
        )
    SocialProfile.objects.create(
        settings=footer, network="mastodon", url="https://social.example/@shop"
    )
    page.set_viewport_size({"width": width, "height": 800})
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    assert page.goto(f"{live_server.url}/").status == 200  # type: ignore[union-attr]
    page.wait_for_load_state("load")
    social = page.locator(".site-footer a[rel~='me']")
    expect(social).to_have_text("Mastodon")
    expect(page.locator(".site-footer__widget")).to_have_count(4)
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    assert csp_violations(page) == [], "\n".join(csp_violations(page))
