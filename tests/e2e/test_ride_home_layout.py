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
from apps.seed import data
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import csp_violations
from tests.e2e.test_ride_home import _ride_home, open_home  # noqa: F401 (autouse fixture)
from tests.e2e.visual import assert_matches_baseline, require_visual_mode

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _demo_landing_with_tiles(_ride_home: None) -> None:  # noqa: F811
    """The body of the seeded vendor-recruitment home (CMS.19, the demo texts of apps/seed/data)
    with one block of tiles added: hero, three text sections, quotes, tiles, FAQ, two bands."""
    home = HomePage.objects.get(locale__language_code=primary_language())
    about = StandardPage.objects.get(locale=home.locale, depth=home.depth + 1)

    def card(label: str, text: str) -> tuple[str, dict[str, object]]:
        return ("page", {"page": about, "label": label, "text": text})

    def band(heading: str, text: str) -> tuple[str, dict[str, object]]:
        link = {"page": None, "url": "/about/", "label": "Apply now"}
        return ("cta", {"heading": heading, "text": text, "link": link})

    hero = (home.body[0].block_type, home.body[0].value)
    home.body = [
        hero,
        ("paragraph", data.LANDING_BENEFITS),
        (
            "featured",
            {
                "heading": "Browse",
                "cards": [
                    card("The shop", "Demo products from our sellers"),
                    card("The blog", "Notes from the workshops"),
                    card("About us", "Who we are"),
                ],
            },
        ),
        ("paragraph", data.LANDING_STEPS),
        ("paragraph", data.LANDING_FEES),
        (
            "strip",
            {
                "heading": data.LANDING_QUOTES_HEADING,
                "cards": [
                    ("testimonial", {"quote": quote, "author": author, "role": role})
                    for quote, author, role in data.LANDING_QUOTES
                ],
                "per_view": "2",
            },
        ),
        (
            "faq",
            {
                "heading": data.LANDING_FAQ_HEADING,
                "items": [{"question": q, "answer": a} for q, a in data.LANDING_FAQ],
            },
        ),
        band(*data.LANDING_CTA),
        band(*data.LANDING_SHOP_CTA),
    ]
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


@pytest.mark.parametrize("width", [375, 1280])
def test_the_home_sections_match_their_visual_baseline(
    width: int, page: Page, live_server: LiveServer
) -> None:
    require_visual_mode()
    open_home(page, live_server, width, 900)
    page.evaluate("document.querySelector('#consent-banner')?.remove()")
    assert_matches_baseline(
        page, theme="ride", name="home-layout", width=width, selector=".site-main"
    )
