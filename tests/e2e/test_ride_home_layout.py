"""E2E (THM.24 part 1/2): the `ride` home layout (spec 14 §14.13).

Image-led tiles with an overlay title, sections on alternating surfaces and a full-bleed band:
axe (`wcag22aa`) clean at 375 and 1280 px, no CSP violation, the tiles reachable and operable
with the keyboard only, and no gap between two sections above `--space-section`. The visual
baselines of the home are the THM.10 ones; the owner approves them on STAGE.
"""

import pytest
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.cms.models import HomePage, StandardPage
from apps.core.i18n import primary_language
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import csp_violations
from tests.e2e.test_ride_home import _ride_home, open_home  # noqa: F401 (autouse fixture)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _tiles_on_the_home(_ride_home: None) -> None:  # noqa: F811
    home = HomePage.objects.get(locale__language_code=primary_language())
    about = StandardPage.objects.get(locale=home.locale, depth=home.depth + 1)
    card = {"page": about, "label": "", "text": "Read more"}
    body = [(child.block_type, child.value) for child in home.body]
    body.insert(1, ("featured", {"heading": "Browse", "cards": [("page", card)] * 3}))
    body.append(("paragraph", "<p>Closing section.</p>"))
    home.body = body
    home.save_revision().publish()


@pytest.mark.parametrize("width", [375, 1280])
def test_the_home_sections_are_clean_for_axe_and_the_csp(
    width: int, page: Page, live_server: LiveServer
) -> None:
    open_home(page, live_server, width, 900)
    expect(page.locator(".tile")).to_have_count(3)
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    assert csp_violations(page) == [], "\n".join(csp_violations(page))


def test_a_tile_is_one_link_reached_with_the_keyboard(page: Page, live_server: LiveServer) -> None:
    open_home(page, live_server, 1280, 900)
    first = page.locator(".tile__title a").first
    first.focus()
    expect(first).to_be_focused()
    outline = first.evaluate("e => getComputedStyle(e).outlineStyle")
    assert outline != "none"
    tile = page.locator(".tile").first.bounding_box()
    link = first.bounding_box()
    assert tile is not None
    assert link is not None
    assert tile["height"] >= 44
    assert link["height"] >= 24
    assert page.locator(".tile a").count() == 3  # one link per tile
    page.keyboard.press("Tab")
    expect(page.locator(".tile__title a").nth(1)).to_be_focused()


@pytest.mark.parametrize("width", [375, 1280])
def test_no_gap_between_two_sections_is_above_the_section_token(
    width: int, page: Page, live_server: LiveServer
) -> None:
    open_home(page, live_server, width, 900)
    gaps = page.evaluate(
        """() => {
          const root = parseFloat(getComputedStyle(document.documentElement).fontSize);
          const probe = document.createElement('div');
          probe.style.blockSize = getComputedStyle(document.documentElement)
            .getPropertyValue('--space-section');
          document.body.append(probe);
          const limit = probe.getBoundingClientRect().height;
          probe.remove();
          const sections = [...document.querySelectorAll('main .home-section')];
          const spans = sections.map((s) => {
            const first = s.firstElementChild.getBoundingClientRect();
            return [first.top, s.lastElementChild.getBoundingClientRect().bottom];
          });
          return {
            limit,
            root,
            // two neighbours only: a band between two sections has its own padding
            gaps: spans.slice(1).map((span, i) => (
              sections[i].nextElementSibling === sections[i + 1] ? span[0] - spans[i][1] : null
            )).filter((gap) => gap !== null),
          };
        }"""
    )
    assert gaps["gaps"], "the home has at least two sections"
    assert all(0 <= gap <= gaps["limit"] + 1 for gap in gaps["gaps"]), gaps
