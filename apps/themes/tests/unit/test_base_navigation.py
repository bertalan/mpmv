"""THM.4 (3/3): navigation components of `base` and the styleguide coverage (spec 14 §14.9).

- Breadcrumb (`components/breadcrumb.html`): a labelled `<nav>` with an ordered list; the last
  item is the current page (`aria-current="page"`, not a link). The BreadcrumbList JSON-LD is
  SEO.1's job, not the component's.
- Pagination (`components/pagination.html`): the CAT.8 listing pagination (previous and next
  with `rel`, "Page N of M") plus optional numbered page links; the current page is marked
  `aria-current="page"`, a gap is an ellipsis and never a link; one page renders nothing.
- Tabs (`components/tabs.html`): without JavaScript every panel is shown under its own heading
  and the tab list stays hidden; the `tabs` Alpine component (CSP build) turns it into a tab
  list with a roving tabindex and arrow keys.
- In-page navigation (`components/in_page_nav.html`): a labelled list of links to sections of
  the page; the "skip" variant shows its links only on focus (skip to filters).
- Coverage: `apps.themes.styleguide.COMPONENTS` mirrors the §14.9 component list: every
  component is shown on the styleguide, with its section, its template and a marker of its
  markup (the integration test checks the marker is rendered in that section).
"""

import re
from pathlib import Path
from typing import Any

import pytest
from django.template.loader import get_template, render_to_string
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.themes.build import render_themes
from apps.themes.styleguide import COMPONENTS
from apps.themes.tests.builders import SHIPPED_THEMES

pytestmark = pytest.mark.usefixtures("english_interface")

ROOT = Path(__file__).resolve().parents[4]
INTERACTIVE = re.compile(r"<(?:a|button|input|select|textarea|details)[\s>]")


@pytest.fixture(autouse=True)
def _base(settings: SettingsWrapper) -> None:
    settings.MPMV_THEME_DEFAULT = "base"


@pytest.fixture(scope="module")
def built() -> dict[str, str]:
    return render_themes(SHIPPED_THEMES)


def render(component: str, /, **context: Any) -> str:
    return render_to_string(f"components/{component}.html", context).strip()


def assert_no_inline_code(html: str) -> None:
    assert "style=" not in html
    assert not re.search(r"\son[a-z]+=", html)
    assert "<script" not in html


# --- Breadcrumb

TRAIL = [
    {"label": "Home", "url": "/"},
    {"label": "Clothing", "url": "/clothing/"},
    {"label": "Touring jacket", "url": "/p/touring-jacket/"},
]


def test_a_breadcrumb_is_a_labelled_nav_with_an_ordered_list() -> None:
    html = render("breadcrumb", items=TRAIL)
    assert re.match(r'<nav class="breadcrumb" aria-label="Breadcrumb">', html), html
    assert "<ol" in html
    assert html.count("<li") == 3


def test_every_breadcrumb_item_but_the_last_is_a_link() -> None:
    html = render("breadcrumb", items=TRAIL)
    assert '<a href="/">Home</a>' in html
    assert '<a href="/clothing/">Clothing</a>' in html
    assert len(INTERACTIVE.findall(html)) == 2


def test_the_last_breadcrumb_item_is_the_current_page_and_not_a_link() -> None:
    html = render("breadcrumb", items=TRAIL)
    assert html.count('aria-current="page"') == 1
    assert re.search(r'<span aria-current="page">\s*Touring jacket\s*</span>', html), html
    assert "/p/touring-jacket/" not in html


def test_a_breadcrumb_leaves_the_json_ld_to_seo() -> None:
    assert "application/ld+json" not in render("breadcrumb", items=TRAIL)


def test_a_breadcrumb_without_items_renders_nothing() -> None:
    assert render("breadcrumb", items=[]) == ""


def test_a_breadcrumb_never_uses_inline_code(built: dict[str, str]) -> None:
    assert_no_inline_code(render("breadcrumb", items=TRAIL))
    assert ".breadcrumb" in built["themes/base/theme.css"]


# --- Pagination


def pagination(**context: Any) -> str:
    return render("pagination", **{"page": 2, "page_count": 5, **context})


def test_pagination_is_a_labelled_nav_with_the_page_in_words() -> None:
    html = pagination(previous_url="?page=1", next_url="?page=3")
    assert re.match(r'<nav class="pagination" aria-label="Pagination">', html), html
    assert "Page 2 of 5" in html


def test_pagination_links_the_previous_and_next_pages_like_the_listing() -> None:
    html = pagination(previous_url="?page=1", next_url="?page=3")
    assert re.search(r'<a [^>]*href="\?page=1"[^>]*rel="prev"', html), html
    assert re.search(r'<a [^>]*href="\?page=3"[^>]*rel="next"', html), html
    assert "Previous" in html
    assert "Next" in html


def test_the_first_page_has_no_previous_link() -> None:
    html = pagination(page=1, next_url="?page=2")
    assert 'rel="prev"' not in html
    assert 'rel="next"' in html
    assert "Page 1 of 5" in html


def test_the_last_page_has_no_next_link() -> None:
    html = pagination(page=5, previous_url="?page=4")
    assert 'rel="next"' not in html
    assert 'rel="prev"' in html


def test_a_single_page_needs_no_pagination() -> None:
    assert render("pagination", page=1, page_count=1) == ""


def test_numbered_pages_mark_the_current_one_and_show_gaps_as_text() -> None:
    pages = [
        {"number": 1, "url": "?page=1"},
        None,
        {"number": 4, "url": "?page=4"},
        {"number": 5, "url": "?page=5"},
        {"number": 6, "url": "?page=6"},
        None,
        {"number": 9, "url": "?page=9"},
    ]
    html = pagination(page=5, page_count=9, previous_url="?page=4", next_url="?page=6", pages=pages)
    assert html.count('aria-current="page"') == 1
    assert re.search(r'<a [^>]*href="\?page=5"[^>]*aria-current="page"', html), html
    assert html.count("…") == 2
    assert not re.search(r"<a [^>]*>\s*…", html)
    # Every number link names its page for screen readers, not just the digit.
    assert re.search(r'<a [^>]*href="\?page=9"[^>]*aria-label="Page 9"', html), html


def test_pagination_never_uses_inline_code(built: dict[str, str]) -> None:
    assert_no_inline_code(pagination(previous_url="?page=1", next_url="?page=3"))
    assert ".pagination" in built["themes/base/theme.css"]


# --- Tabs

PANELS = [
    {"label": "Description", "content": "A light jacket for touring."},
    {"label": "Size guide", "content": "Measure your chest."},
    {"label": "Care", "content": "Wash at 30 degrees."},
]


def tabs(**context: Any) -> str:
    return render(
        "tabs", **{"id": "product", "label": "Product details", "tabs": PANELS, **context}
    )


def test_tabs_render_a_hidden_tab_list_and_every_panel_with_a_heading() -> None:
    html = tabs()
    tablist = re.search(r'<div class="tabs__list"[^>]*>', html)
    assert tablist is not None
    assert 'role="tablist"' in tablist.group(0)
    assert 'aria-label="Product details"' in tablist.group(0)
    # Without JavaScript the list stays hidden and every panel shows under its heading.
    assert re.search(r"\shidden[\s>]", tablist.group(0))
    assert html.count('class="tabs__panel"') == 3
    for panel in PANELS:
        assert re.search(rf'class="tabs__heading"[^>]*>\s*{panel["label"]}\s*<', html), html
        assert panel["content"] in html


def test_each_tab_controls_its_panel_and_the_ids_are_prefixed() -> None:
    html = tabs()
    buttons = re.findall(r'<button[^>]*role="tab"[^>]*>', html)
    assert len(buttons) == 3
    for index, button in enumerate(buttons, start=1):
        assert 'type="button"' in button
        assert f'id="product-tab-{index}"' in button
        assert f'aria-controls="product-panel-{index}"' in button
        assert f'id="product-panel-{index}"' in html
        assert f'aria-labelledby="product-tab-{index}"' in html


def test_the_first_tab_is_selected_and_the_only_tab_stop() -> None:
    buttons = re.findall(r'<button[^>]*role="tab"[^>]*>', tabs())
    assert 'aria-selected="true"' in buttons[0]
    assert 'tabindex="0"' in buttons[0]
    for button in buttons[1:]:
        assert 'aria-selected="false"' in button
        assert 'tabindex="-1"' in button


def test_another_tab_can_be_selected_on_load() -> None:
    buttons = re.findall(r'<button[^>]*role="tab"[^>]*>', tabs(selected=2))
    assert 'aria-selected="true"' in buttons[1]
    assert 'aria-selected="false"' in buttons[0]


def test_tabs_use_the_alpine_csp_component_without_inline_code(built: dict[str, str]) -> None:
    html = tabs()
    assert 'x-data="tabs"' in html
    # The CSP build evaluates names only: no call, no expression in a directive.
    for value in re.findall(r'\s(?:x-[a-z:.-]+|@[a-z.-]+|:[a-z-]+)="([^"]*)"', html):
        assert re.fullmatch(r"[A-Za-z_$][\w$]*", value), value
    assert_no_inline_code(html)
    module = built["themes/base/js/base.js"]
    assert 'Alpine.data("tabs"' in module
    for key in ("ArrowRight", "ArrowLeft", "Home", "End"):
        assert key in module
    assert ".tabs__list" in built["themes/base/theme.css"]


# --- In-page navigation


SECTIONS = [{"label": "Filters", "url": "#filters"}, {"label": "Products", "url": "#products"}]


def test_in_page_navigation_is_a_labelled_list_of_anchors() -> None:
    html = render("in_page_nav", label="On this page", links=SECTIONS)
    assert re.match(r'<nav class="in-page-nav" aria-label="On this page">', html), html
    assert '<a href="#filters">Filters</a>' in html
    assert '<a href="#products">Products</a>' in html


def test_the_skip_variant_shows_its_links_only_on_focus(built: dict[str, str]) -> None:
    html = render(
        "in_page_nav",
        label="Skip links",
        links=[{"label": "Skip to filters", "url": "#filters"}],
        variant="skip",
    )
    assert 'class="in-page-nav in-page-nav--skip"' in html
    assert_no_inline_code(html)
    css = built["themes/base/theme.css"]
    assert ".in-page-nav--skip" in css
    assert re.search(r"\.in-page-nav--skip a:focus", css)


def test_in_page_navigation_without_links_renders_nothing() -> None:
    assert render("in_page_nav", label="On this page", links=[]) == ""


# --- Every §14.9 component is on the styleguide


def spec_components() -> list[str]:
    """The §14.9 component list, names without their parenthesised details."""
    text = (ROOT / "docs/spec/14-frontend-themes.md").read_text(encoding="utf-8")
    match = re.search(r"Component library in `base` \(.*?\): (.*?)\.\n\n", text, re.DOTALL)
    assert match is not None, "the §14.9 component list moved"
    flat = re.sub(r"\s*\([^)]*\)", "", " ".join(match.group(1).split()))
    return [name.strip() for name in flat.split(",")]


def test_every_spec_component_is_on_the_styleguide() -> None:
    assert sorted(COMPONENTS) == sorted(spec_components())


def test_every_shown_component_names_a_section_a_template_and_a_marker() -> None:
    for name, shown in COMPONENTS.items():
        assert shown.section.startswith("sg-"), name
        assert get_template(shown.template), (name, shown.template)
        assert shown.marker.startswith('class="'), name


def test_the_navigation_components_are_shown() -> None:
    for name in (
        "breadcrumb",
        "pagination",
        "tabs",
        "skip link",
        "header and disclosure navigation",
    ):
        assert name in COMPONENTS, name
