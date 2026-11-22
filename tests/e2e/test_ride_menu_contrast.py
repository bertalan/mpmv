"""E2E (THM.15): the `ride` menu text is readable on every page and in every menu state.

The header links, the dropdown sub-menu items, the offcanvas menu, the search overlay and the
sticky bar after a scroll are measured at 360, 768 and 1280 px on the home page (with and without
a hero), a listing, the product page, a CMS standard page and the cart. Every visible text in
those regions must reach WCAG AA 4.5:1 against its effective background: the first opaque
background of the element or an ancestor, with translucent layers composited on top of it. The
colours come from the computed styles (a canvas turns any CSS colour into sRGB bytes).

The regression: Bootstrap declares `--bs-dropdown-bg` on `.dropdown-menu` itself, so the value the
theme set on `.site-header` never reached the sub-menu, which kept the white body background
while the header links stayed white. The close buttons of the offcanvas and the search overlay
must show the sprite X (the CSP blocks Bootstrap's data: URI) at 3:1 or more, with an aria-label.
`MPMV_CONTRAST_SHOTS=<dir>` saves a screenshot per state.
"""

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from playwright.sync_api import Page, expect
from pytest_django.fixtures import Settings as SettingsWrapper
from pytest_django.live_server_helper import LiveServer
from wagtail.models import Locale

from apps.cms.models import HomePage
from apps.cms.templatetags import cms_tags
from tests.e2e.test_visual_baselines import paths  # noqa: F401 (fixture)
from tests.e2e.visual import WIDTHS

pytestmark = pytest.mark.django_db(transaction=True)

AA_TEXT = 4.5
MENU = [
    SimpleNamespace(label="Shop", url="/product-category/vases/", children=[]),
    SimpleNamespace(
        label="Vendors",
        url="/vendors/",
        children=[
            SimpleNamespace(label="All vendors", url="/vendors/"),
            SimpleNamespace(label="Become a vendor", url="/sell/"),
        ],
    ),
    SimpleNamespace(label="About", url="/about/", children=[]),
]
PAGES = {
    "home": "/",
    "home-hero": "/",
    "listing": "/product-category/vases/",
    "product": "/product/blue-vase/",
    "standard": "/about/",
    "cart": "/cart/",
}
# The category and product fixture exist in English only; the standard page is seeded per language.
ENGLISH_CONTENT = {"listing", "product", "standard"}
HERO = {
    "heading": "Ride better",
    "text": "Gear from small workshops.",
    "image": None,
    "alt_text": "",
    "layout": "single",
    "slides": [],
    "links": [{"page": None, "url": "/about/", "label": "Shop now"}],
}

# Every visible element with its own text inside `root`, with the contrast of its colour against
# its effective background. Background images are not measured (none sits under the menu).
CONTRAST_JS = """
(root) => {
  const ctx = document.createElement('canvas').getContext('2d', {willReadFrequently: true});
  const rgba = (css) => {
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = '#000';
    ctx.fillStyle = css;
    ctx.fillRect(0, 0, 1, 1);
    const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data;
    return [r, g, b, a / 255];
  };
  const parse = (css) => {
    if (css === 'transparent' || css === 'rgba(0, 0, 0, 0)') return [0, 0, 0, 0];
    const m = css.match(/rgba?\\(([^)]+)\\)/);
    if (m) {
      const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
      return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
    }
    return rgba(css);
  };
  const over = (top, bottom) => {
    const a = top[3];
    return [0, 1, 2].map((i) => top[i] * a + bottom[i] * (1 - a)).concat([1]);
  };
  const background = (el) => {
    const layers = [];
    for (let node = el; node && node.nodeType === 1; node = node.parentElement) {
      const bg = parse(getComputedStyle(node).backgroundColor);
      if (bg[3] > 0) layers.push(bg);
      if (bg[3] >= 1) break;
    }
    let colour = [255, 255, 255, 1];
    for (const layer of layers.reverse()) colour = over(layer, colour);
    return colour;
  };
  const lum = ([r, g, b]) => {
    const f = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const visible = (el) => {
    const s = getComputedStyle(el);
    if (s.visibility !== 'visible' || s.opacity === '0') return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    if (el.closest('.visually-hidden, [hidden]')) return false;
    return true;
  };
  const out = [];
  for (const el of root.querySelectorAll('*')) {
    const text = Array.from(el.childNodes)
      .filter((n) => n.nodeType === 3).map((n) => n.textContent.trim()).join(' ').trim();
    if (!text || !visible(el)) continue;
    const bg = background(el);
    const fg = over(parse(getComputedStyle(el).color), bg);
    const [l1, l2] = [lum(fg), lum(bg)].sort((a, b) => b - a);
    out.push({
      text: text.slice(0, 40),
      ratio: Math.round(((l1 + 0.05) / (l2 + 0.05)) * 100) / 100,
      fg: fg.slice(0, 3).map(Math.round).join(','),
      bg: bg.slice(0, 3).map(Math.round).join(','),
    });
  }
  return out;
}
"""


@pytest.fixture(autouse=True)
def _ride_with_submenu(
    settings: SettingsWrapper, live_server: LiveServer, _cms_site: None, monkeypatch: Any
) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"
    monkeypatch.setattr(cms_tags, "_menu_links", lambda request, menu: list(MENU))


def add_hero() -> None:
    home = HomePage.objects.get(locale=Locale.get_default())
    home.body = [("hero", HERO)]
    home.save_revision().publish()


def failures(page: Page, selector: str) -> list[str]:
    bad: list[str] = []
    for root in page.locator(selector).all():
        if not root.is_visible():
            continue
        for item in root.evaluate(CONTRAST_JS):
            if item["ratio"] < AA_TEXT:
                colours = f"{item['fg']} on {item['bg']}"
                bad.append(f"{selector} {item['text']!r}: {item['ratio']} ({colours})")
    return bad


def snap(page: Page, name: str) -> None:
    folder = os.environ.get("MPMV_CONTRAST_SHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(folder) / f"{name}.png"))


# The close button's X: drawn from the same-origin sprite (the CSP blocks Bootstrap's data: URI),
# with no background image, and its stroke at least 3:1 on the panel (WCAG 1.4.11).
CLOSE_JS = """
(button) => {
  const ctx = document.createElement('canvas').getContext('2d', {willReadFrequently: true});
  const rgb = (css) => {
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = '#000';
    ctx.fillStyle = css;
    ctx.fillRect(0, 0, 1, 1);
    const d = ctx.getImageData(0, 0, 1, 1).data; return [d[0], d[1], d[2], d[3] / 255];
  };
  let bg = [255, 255, 255];
  for (let n = button; n && n.nodeType === 1; n = n.parentElement) {
    const c = rgb(getComputedStyle(n).backgroundColor);
    if (c[3] >= 1) { bg = c.slice(0, 3); break; }
  }
  const icon = button.querySelector('svg.icon');
  const use = icon && icon.querySelector('use');
  const lum = ([r, g, b]) => {
    const f = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const fg = icon ? rgb(getComputedStyle(icon).color).slice(0, 3) : bg;
  const [l1, l2] = [lum(fg), lum(bg)].sort((a, b) => b - a);
  const ratio = (a, b) => {
    const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m);
    return Math.round(((x + 0.05) / (y + 0.05)) * 100) / 100;
  };
  // The panel behind the button (its parents), the button's own fill, the icon, the outline.
  let panel = [255, 255, 255];
  for (let n = button.parentElement; n && n.nodeType === 1; n = n.parentElement) {
    const c = rgb(getComputedStyle(n).backgroundColor);
    if (c[3] >= 1) { panel = c.slice(0, 3); break; }
  }
  const own = rgb(getComputedStyle(button).backgroundColor);
  const fill = own[3] >= 1 ? own.slice(0, 3) : panel;
  const outline = getComputedStyle(button);
  return {
    iconOnFill: ratio(fg, fill),
    fillOnPanel: ratio(fill, panel),
    outlineOnPanel: ratio(rgb(outline.outlineColor).slice(0, 3), panel),
    outlineWidth: parseFloat(outline.outlineWidth),
    outlineStyle: outline.outlineStyle,
    label: button.getAttribute('aria-label'),
    href: use ? use.getAttribute('href') : '',
    bgImage: outline.backgroundImage,
    filter: outline.filter,
    iconWidth: icon ? icon.getBoundingClientRect().width : 0,
    ratio: Math.round(((l1 + 0.05) / (l2 + 0.05)) * 100) / 100,
  };
}
"""


MEASURED: list[str] = []  # printed with -s: the ratios behind the PR comment


def close_failures(page: Page, selector: str) -> list[str]:
    button = page.locator(f"{selector} .btn-close")
    state = button.evaluate(CLOSE_JS)
    bad = []
    if not state["label"]:
        bad.append(f"{selector} close button: no aria-label")
    if not state["href"].endswith("#close") or state["iconWidth"] < 12:
        bad.append(f"{selector} close button: no visible sprite X ({state})")
    if state["bgImage"] != "none" or state["filter"] != "none":
        bad.append(f"{selector} close button: Bootstrap's data: URI or filter applies ({state})")
    if state["ratio"] < 3:
        bad.append(f"{selector} close button: X at {state['ratio']}:1")
    bad += close_state_failures(page, selector, button)
    return bad


def close_state_failures(page: Page, selector: str, button: Any) -> list[str]:
    """Hover and keyboard focus: the icon stays at AA on the button's fill, the hover fill is a
    visible change (3:1 against the panel), the focus outline is drawn at 3:1 against the panel."""
    bad = []
    button.hover()
    page.wait_for_timeout(300)
    hover = button.evaluate(CLOSE_JS)
    MEASURED.append(
        f"{selector} hover: icon {hover['iconOnFill']}:1, fill {hover['fillOnPanel']}:1"
    )
    if hover["iconOnFill"] < AA_TEXT:
        bad.append(f"{selector} close hover: X at {hover['iconOnFill']}:1 on its fill")
    if hover["fillOnPanel"] < 3:
        bad.append(f"{selector} close hover: fill at {hover['fillOnPanel']}:1 on the panel")
    page.mouse.move(0, 0)
    button.focus()
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Tab")  # focus-visible needs a keyboard-driven focus
    page.wait_for_timeout(300)
    focus = button.evaluate(CLOSE_JS)
    MEASURED.append(
        f"{selector} focus: icon {focus['iconOnFill']}:1, outline {focus['outlineOnPanel']}:1"
    )
    if focus["outlineStyle"] == "none" or focus["outlineWidth"] < 2:
        bad.append(f"{selector} close focus-visible: no outline ({focus})")
    if focus["outlineOnPanel"] < 3:
        bad.append(f"{selector} close focus: outline at {focus['outlineOnPanel']}:1 on the panel")
    if focus["iconOnFill"] < 3:
        bad.append(f"{selector} close focus: X at {focus['iconOnFill']}:1")
    return bad


def open_dropdown(page: Page) -> None:
    toggle = page.locator(".site-header [data-bs-toggle='dropdown']").first
    toggle.click()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(page.locator(".site-header .dropdown-menu.show")).to_be_visible()


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("name", PAGES)
def test_menu_text_reaches_aa_contrast_in_every_state(
    name: str,
    width: int,
    settings: SettingsWrapper,
    page: Page,
    live_server: LiveServer,
    paths: dict,  # type: ignore[type-arg]  # noqa: F811
) -> None:
    if name == "home-hero":
        add_hero()
    page.emulate_media(reduced_motion="reduce")
    page.set_viewport_size({"width": width, "height": 800})
    url = PAGES[name]
    if name in ENGLISH_CONTENT and "en" not in [code for code, _ in settings.LANGUAGES]:
        pytest.skip("the fixture content is English and English is not served in this run")
    response = page.goto(f"{live_server.url}{url}")
    assert response is not None
    assert response.status == 200
    page.wait_for_load_state("load")
    tag = f"{name}-{width}"
    bad = failures(page, ".site-header")
    snap(page, f"{tag}-bar")

    if width < 1024:
        page.locator(".nav-toggle").click()
        panel = page.locator("#site-offcanvas")
        expect(panel).to_be_visible()
        page.wait_for_timeout(400)  # the slide-in transition
        bad += failures(page, "#site-offcanvas")
        bad += close_failures(page, "#site-offcanvas")
        snap(page, f"{tag}-offcanvas")
        open_dropdown(page)
        bad += failures(page, "#site-offcanvas")
        snap(page, f"{tag}-offcanvas-dropdown")
        page.keyboard.press("Escape")
        page.keyboard.press("Escape")
        expect(panel).to_be_hidden()
    else:
        open_dropdown(page)
        bad += failures(page, ".site-header .dropdown-menu")
        snap(page, f"{tag}-dropdown")
        page.keyboard.press("Escape")

    page.locator(".site-header a[data-bs-target='#search-modal']").click()
    modal = page.locator("#search-modal")
    expect(modal).to_be_visible()
    page.wait_for_timeout(400)
    bad += failures(page, "#search-modal")
    bad += close_failures(page, "#search-modal")
    snap(page, f"{tag}-search")
    page.keyboard.press("Escape")
    expect(modal).to_be_hidden()

    page.mouse.wheel(0, 1200)
    page.wait_for_timeout(200)
    bad += failures(page, ".site-header")
    snap(page, f"{tag}-scrolled")

    print("\n".join(sorted(set(MEASURED))))
    assert bad == [], "\n".join(bad)
