"""E2E (THM.22): the `ride` footer and consent card in the browser.

No blank band below the footer on the key pages, the consent card covers at most 30% of a
375x812 viewport, text and links of the footer and the card pass 4.5:1 on their own surface,
axe and the CSP stay quiet. Visual baselines of the footer: tests/e2e/baselines/ride/
`footer-375.png` and `footer-1280.png` (CI only, tests/e2e/visual.py).
"""

import pytest
from playwright.sync_api import Page, expect
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer
from wagtail.models import Site

from apps.catalog.models import Product
from apps.catalog.tests.factories import brand_logo_image
from apps.cms.models import FooterColumn, FooterSettings, SocialProfile
from apps.core.i18n import primary_language
from apps.themes.engine import contrast_ratio
from apps.vendors.models import Vendor
from apps.vendors.tests.factories import VendorFactory
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations
from tests.e2e.test_add_to_cart import product  # noqa: F401 (fixture)
from tests.e2e.test_catalog_listings import term_page  # noqa: F401 (fixture)
from tests.e2e.test_ride_overlays import ride_paths
from tests.e2e.visual import assert_matches_baseline, require_visual_mode

pytestmark = pytest.mark.django_db(transaction=True)

PAINT = """(sel) => {
  const el = document.querySelector(sel);
  const c = (e, p) => getComputedStyle(e)[p];
  let bg = el, back = c(bg, 'backgroundColor');
  while (back === 'rgba(0, 0, 0, 0)' && bg.parentElement) {
    bg = bg.parentElement; back = c(bg, 'backgroundColor');
  }
  const link = el.querySelector('a');
  return {bg: back, text: c(el.querySelector('p'), 'color'), link: c(link, 'color')};
}"""


def hexed(colour: str) -> str:
    r, g, b = (
        int(float(v)) for v in colour[colour.index("(") + 1 : colour.index(")")].split(",")[:3]
    )
    return f"#{r:02X}{g:02X}{b:02X}"


@pytest.fixture(autouse=True)
def _ride(settings: SettingsWrapper, live_server: LiveServer, _cms_site: None) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    settings.MPMV_HELP_URL = "/about/#help"


def visit(page: Page, live_server: LiveServer, path: str, width: int, height: int = 812) -> None:
    page.emulate_media(reduced_motion="reduce")
    page.set_viewport_size({"width": width, "height": height})
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.goto(f"{live_server.url}{path}")
    page.wait_for_load_state("load")


@pytest.mark.parametrize("where", ["home", "listing", "product"])
@pytest.mark.parametrize("width", [375, 1280])
def test_there_is_no_blank_band_below_the_footer(
    where: str,
    width: int,
    page: Page,
    live_server: LiveServer,
    term_page: None,  # noqa: F811
    product: Product,  # noqa: F811
) -> None:
    visit(page, live_server, ride_paths()[where], width)
    page.evaluate("document.querySelector('#consent-banner').remove()")
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    gap = page.evaluate(
        "document.documentElement.scrollHeight"
        " - document.querySelector('.site-footer').getBoundingClientRect().bottom"
        " - window.scrollY"
    )
    assert abs(gap) <= 1, f"{gap}px below the footer"


@pytest.mark.parametrize("width", [375, 1280])
def test_the_footer_text_and_links_pass_on_their_own_surface(
    width: int, page: Page, live_server: LiveServer
) -> None:
    visit(page, live_server, "/", width)
    read = page.evaluate(PAINT, ".site-footer__top .site-footer__col")
    bg = hexed(read["bg"])
    for part in ("text", "link"):
        assert contrast_ratio(hexed(read[part]), bg) >= 4.5, part
    violations = run_axe(page, include=".site-footer")
    assert not violations, format_violations(violations)


@pytest.mark.consent_banner
def test_the_consent_card_covers_at_most_30_percent_of_a_phone_viewport(
    page: Page, live_server: LiveServer
) -> None:
    visit(page, live_server, "/", 375, 812)
    card = page.locator("#consent-banner")
    expect(card).to_be_visible()
    box = card.bounding_box()
    assert box
    assert box["width"] * box["height"] <= 0.30 * 375 * 812, box
    reject = card.get_by_role("button", name="Reject all")
    accept = card.get_by_role("button", name="Accept all")
    assert reject.evaluate("e => getComputedStyle(e).backgroundColor") == accept.evaluate(
        "e => getComputedStyle(e).backgroundColor"
    )
    read = page.evaluate(PAINT, "#consent-banner form")
    bg = hexed(read["bg"])
    for part in ("text", "link"):
        assert contrast_ratio(hexed(read[part]), bg) >= 4.5, part
    violations = run_axe(page, include="#consent-banner")
    assert not violations, format_violations(violations)
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


@pytest.mark.parametrize("width", [375, 1280])
def test_the_footer_with_strip_columns_and_social_icons_matches_its_baseline(
    width: int, page: Page, live_server: LiveServer
) -> None:
    require_visual_mode()
    for name in ("Alpine Works", "Berg Cycles", "Cima Gear"):
        vendor = VendorFactory.create(display_name=name, status=Vendor.Status.ACTIVE)
        Vendor.objects.filter(pk=vendor.pk).update(logo=brand_logo_image())
    footer = FooterSettings.objects.create(site=Site.objects.get(is_default_site=True))
    for n in range(3):
        FooterColumn.objects.create(
            settings=footer,
            language=primary_language(),
            title=f"Column {n}",
            links=[("external", {"url": f"https://example.com/{n}/", "label": f"Link {n}"})],
        )
    for network in ("mastodon", "instagram", "github"):
        SocialProfile.objects.create(
            settings=footer, network=network, url=f"https://social.example/{network}"
        )
    visit(page, live_server, "/", width)
    expect(page.locator(".site-footer-strip .logo-strip li")).to_have_count(3)
    expect(page.locator(".site-footer__social svg")).to_have_count(3)
    page.evaluate("document.querySelector('#consent-banner')?.remove()")
    assert_matches_baseline(
        page, theme="ride", name="footer", width=width, selector=".site-footer-group"
    )
