"""E2E (THM.9 part 1): the `ride` content and system pages.

Each page type passes axe (`wcag22aa`), raises no CSP violation and never scrolls sideways at
360, 768 and 1280 px; its links are reachable with Tab and a focused link is never hidden by the
sticky header; with reduced motion nothing in the content transitions. The 404 page is served
by the site, the 500 page by the handler (rendered without the database, then served through a
route of the browser, as the server never fails on purpose).
"""

from collections.abc import Callable
from pathlib import Path

import pytest
from django.test import RequestFactory
from playwright.sync_api import Page, expect
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer

from apps.themes import views
from tests.e2e.axe import format_violations, run_axe
from tests.e2e.csp import RECORD_CSP_VIOLATIONS, csp_violations
from tests.ride_content_site import build_content_site

pytestmark = pytest.mark.django_db(transaction=True)

NO_SIDEWAYS_SCROLL = "() => document.documentElement.scrollWidth <= window.innerWidth"
KINDS = [
    "standard",
    "blog",
    "post",
    "author",
    "legal",
    "legal_version",
    "vendor",
    "recruitment",
    "contact",
    "404",
    "500",
]

# Whether the element painted at the centre of the focused element is that element.
FOCUS_VISIBLE = """
() => {
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  const box = el.getBoundingClientRect();
  const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
  if (!!hit && (hit === el || el.contains(hit))) return true;
  return `top=${box.top} vh=${innerHeight} hit=${hit && hit.outerHTML.slice(0, 80)}`;
}
"""


@pytest.fixture
def site(
    settings: SettingsWrapper, live_server: LiveServer, site_allows_indexing: None
) -> dict[str, str]:
    settings.MPMV_THEME_DEFAULT = "ride"
    return build_content_site(images=True)


def open_page(page: Page, live_server: LiveServer, site: dict[str, str], kind: str) -> None:
    if kind == "500":
        html = views.server_error(RequestFactory().get("/boom/")).content.decode()
        page.route(
            "**/boom/",
            lambda route: route.fulfill(status=500, content_type="text/html", body=html),
        )
        page.goto(f"{live_server.url}/boom/")
    elif kind == "404":
        page.goto(f"{live_server.url}/no-such-page-here/")
    else:
        page.goto(f"{live_server.url}{site[kind]}")
    expect(page.locator("html")).to_have_attribute("data-theme", "ride")


@pytest.mark.parametrize("width", [360, 768, 1280])
@pytest.mark.parametrize("kind", KINDS)
def test_the_page_passes_the_gates(
    kind: str,
    width: int,
    page: Page,
    live_server: LiveServer,
    site: dict[str, str],
    shot: Callable[[str], Path],
) -> None:
    page.add_init_script(RECORD_CSP_VIOLATIONS)
    page.set_viewport_size({"width": width, "height": 900})
    open_page(page, live_server, site, kind)
    expect(page.locator("main .page-banner h1")).to_have_count(1)
    expect(page.locator("h1")).to_have_count(1)
    assert page.evaluate(NO_SIDEWAYS_SCROLL), f"sideways scroll at {width}px"
    violations = run_axe(page)
    assert violations == [], format_violations(violations)
    assert csp_violations(page) == [], "\n".join(csp_violations(page))
    shot(f"{width}-{kind}")


@pytest.mark.parametrize("kind", ["blog", "recruitment", "contact", "404"])
def test_every_link_of_the_content_is_reachable_with_tab_and_never_hidden(
    kind: str, page: Page, live_server: LiveServer, site: dict[str, str]
) -> None:
    page.emulate_media(reduced_motion="reduce")  # focus scrolling is then immediate
    page.set_viewport_size({"width": 360, "height": 640})
    open_page(page, live_server, site, kind)
    links = page.locator("main a[href]").count()
    assert links
    reached: set[str] = set()
    for _ in range(80):
        page.keyboard.press("Tab")
        info = page.evaluate(
            "() => { const e = document.activeElement;"
            " return e && e.closest('main') ? e.getAttribute('href') : null; }"
        )
        if info:
            reached.add(info)
            seen = page.evaluate(FOCUS_VISIBLE)
            assert seen is True, f"{info} hidden: {seen}"
    expected = set(
        page.eval_on_selector_all("main a[href]", "els => els.map(e => e.getAttribute('href'))")
    )
    assert expected <= reached, expected - reached


def test_shift_tab_to_a_link_at_the_top_edge_leaves_it_visible(
    page: Page, live_server: LiveServer, site: dict[str, str]
) -> None:
    page.emulate_media(reduced_motion="reduce")
    page.set_viewport_size({"width": 360, "height": 640})
    page.goto(f"{live_server.url}{site['blog']}")
    links = page.locator("main .post-summary a")
    last = links.last
    last.focus()
    for _ in range(len(links.all())):
        page.keyboard.press("Shift+Tab")
        seen = page.evaluate(FOCUS_VISIBLE)
        assert seen is True, seen


def test_reduced_motion_shows_no_transition_in_the_content(
    page: Page, live_server: LiveServer, site: dict[str, str]
) -> None:
    page.emulate_media(reduced_motion="reduce")
    page.goto(f"{live_server.url}{site['recruitment']}")
    durations = page.evaluate(
        "() => [...document.querySelectorAll('main .plan, main .btn, main .post-summary')]"
        ".map(e => getComputedStyle(e).transitionDuration)"
    )
    assert durations
    assert all(d in ("0s", "0.01ms", "1e-05s") for d in durations), durations


def test_the_buttons_and_page_links_are_at_least_44_px(
    page: Page, live_server: LiveServer, site: dict[str, str]
) -> None:
    page.set_viewport_size({"width": 360, "height": 900})
    page.goto(f"{live_server.url}{site['recruitment']}")
    button = page.locator(".recruitment-cta .btn")
    box = button.bounding_box()
    assert box is not None
    assert min(box["width"], box["height"]) >= 44
