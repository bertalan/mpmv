"""E2E (THM.20): the `ride` surfaces in the styleguide, under each predefined palette.

The section shows every surface with its eyebrow, heading, text, link and button; the browser
computes the colours the token contract promises (WCAG contrast of each pair, measured on the
rendered page), axe finds nothing, the textures load from the same origin and the CSP stays
quiet. Visual baselines of the section: `tests/e2e/baselines/ride/styleguide-surfaces-*.png`
(CI only, tests/e2e/visual.py).
"""

import pytest
from playwright.sync_api import Page
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer

from apps.themes.engine import contrast_ratio
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import csp_violations
from tests.e2e.palette import PALETTES, assert_palette_applied, choose_palette
from tests.e2e.test_theme_quality_gates import styleguide_with
from tests.e2e.visual import assert_matches_baseline, require_visual_mode

pytestmark = pytest.mark.django_db(transaction=True)

SECTION = "#sg-surfaces"
SURFACES = ("light", "muted", "band", "primary")
WIDTHS = (375, 1280)
READ = """(sel) => {
  const box = document.querySelector(sel);
  const rgb = (el, prop) => getComputedStyle(el)[prop];
  const part = (q) => box.querySelector(q);
  return {
    bg: rgb(box, 'backgroundColor'), text: rgb(part('p:not(.eyebrow)'), 'color'),
    heading: rgb(part('h3'), 'color'), link: rgb(part('p a'), 'color'),
    eyebrow: rgb(part('.eyebrow'), 'color'), button: rgb(part('.button'), 'color'),
    buttonBg: rgb(part('.button'), 'backgroundColor'),
  };
}"""


def hexed(colour: str) -> str:
    r, g, b = (
        int(float(v)) for v in colour[colour.index("(") + 1 : colour.index(")")].split(",")[:3]
    )
    return f"#{r:02X}{g:02X}{b:02X}"


def check_surfaces(page: Page) -> None:
    for surface in SURFACES:
        read = page.evaluate(
            READ, f"{SECTION} .surface-{surface}:not(.texture-topo):not(.edge-torn)"
        )
        bg = hexed(read["bg"])
        for part in ("text", "heading", "link", "eyebrow"):
            ratio = contrast_ratio(hexed(read[part]), bg)
            assert ratio >= 4.5, f"{surface} {part}: {ratio:.2f}"
        assert contrast_ratio(hexed(read["button"]), hexed(read["buttonBg"])) >= 4.5, surface


@pytest.mark.parametrize("palette", ["red", *PALETTES])
def test_every_surface_passes_in_the_browser_and_axe_is_clean(
    palette: str, page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    if palette != "red":
        choose_palette(palette)
    styleguide_with("ride", page, live_server, settings)
    if palette != "red":
        assert_palette_applied(page, palette)
    section = page.locator(SECTION)
    section.scroll_into_view_if_needed()
    assert section.locator("h2").inner_text()
    for surface in SURFACES:
        assert page.locator(f"{SECTION} .surface-{surface}").count() >= 1
    check_surfaces(page)
    assert run_axe(page, include=SECTION) == [], format_violations(run_axe(page, include=SECTION))
    assert csp_violations(page) == []


def test_the_textures_load_from_the_same_origin(
    page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    styleguide_with("ride", page, live_server, settings)
    topo = page.evaluate(
        "(s) => getComputedStyle(document.querySelector(s + ' .texture-topo')).backgroundImage",
        SECTION,
    )
    mask = page.evaluate(
        "(s) => getComputedStyle(document.querySelector(s + ' .edge-torn'), '::before').maskImage",
        SECTION,
    )
    for value, name in ((topo, "texture-topo.svg"), (mask, "edge-torn.svg")):
        assert name in value, value
        url = value[value.index('"') + 1 : value.rindex('"')]
        assert url.startswith(live_server.url), url
        status = page.evaluate("(u) => fetch(u).then((r) => r.status)", url)
        assert status == 200, url
    assert csp_violations(page) == []


@pytest.mark.parametrize("width", WIDTHS)
def test_the_surfaces_section_matches_its_visual_baseline(
    width: int, page: Page, live_server: LiveServer, settings: SettingsWrapper
) -> None:
    require_visual_mode()
    styleguide_with("ride", page, live_server, settings)
    # The sticky header would paint over the section in an element screenshot (CSSOM is allowed).
    page.evaluate("() => { document.querySelector('header').style.position = 'static'; }")
    assert_matches_baseline(
        page, theme="ride", name="styleguide-surfaces", width=width, selector=SECTION
    )
