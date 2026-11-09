"""THM.6: the template overrides of `ride` (spec 14 §14.6, §14.9): header, hero, footer, icons.

Rendered with the `ride` theme active, without a request: the shell parts are pure templates.
"""

import re
from typing import Any

from django.template.loader import render_to_string

from apps.themes.services import use_theme


def render(name: str, /, **context: Any) -> str:
    with use_theme("ride"):
        return render_to_string(name, context).strip()


LINKS = [
    {"label": "Shop", "url": "/shop/"},
    {
        "label": "Vendors",
        "url": "/vendors/",
        "children": [
            {"label": "All vendors", "url": "/vendors/"},
            {"label": "Become a vendor", "url": "/sell/"},
        ],
    },
]


def test_the_header_is_a_dark_bar_with_the_logo_first_and_a_labelled_nav() -> None:
    html = render("components/header.html", site_name="Ride", links=LINKS)
    assert html.startswith("<header")
    assert 'class="site-header' in html
    assert html.index('class="site-name') < html.index("<nav")
    assert re.search(r'<nav[^>]+aria-label="Main"', html)


def test_a_submenu_is_a_button_with_aria_expanded_never_hover_only() -> None:
    html = render("components/header.html", site_name="Ride", links=LINKS)
    buttons = re.findall(r"<button[^>]*>", html)
    sub = [b for b in buttons if "aria-controls" in b and "nav-toggle" not in b]
    assert len(sub) == 1
    assert 'aria-expanded="false"' in sub[0]
    assert 'type="button"' in sub[0]
    # the parent link stays reachable, and the children are plain links in a list
    assert 'href="/vendors/"' in html
    assert 'href="/sell/"' in html
    assert "onmouseover" not in html


def test_the_burger_button_names_the_menu_and_controls_the_nav() -> None:
    html = render("components/header.html", site_name="Ride", links=LINKS)
    toggle = re.search(r'<button[^>]*class="nav-toggle[^>]*>.*?</button>', html, re.S)
    assert toggle is not None
    assert 'aria-controls="site-nav"' in toggle.group(0)
    assert 'aria-expanded="false"' in toggle.group(0)
    assert "Menu" in toggle.group(0)


def test_search_and_cart_are_icons_with_text_for_screen_readers() -> None:
    html = render(
        "components/header.html",
        site_name="Ride",
        links=LINKS,
        search_url="/search/",
        cart_url="/cart/",
    )
    search = re.search(r'<a[^>]*href="/search/"[^>]*>.*?</a>', html, re.S)
    cart = re.search(r'<a[^>]*href="/cart/"[^>]*>.*?</a>', html, re.S)
    assert search
    assert cart
    assert "<svg" in search.group(0)
    assert re.search(r'class="visually-hidden">\s*Search', search.group(0))
    assert "Cart" in cart.group(0)
    assert 'aria-hidden="true"' in search.group(0)


def test_without_search_and_cart_urls_no_dead_link_is_rendered() -> None:
    html = render("components/header.html", site_name="Ride", links=LINKS)
    assert 'href="#"' not in html
    assert 'href=""' not in html


def test_the_header_has_no_popup_markup() -> None:
    html = render("components/header.html", site_name="Ride", links=LINKS)
    assert "<dialog" not in html
    assert "popup" not in html.lower()


def test_the_hero_has_one_h1_a_lead_a_primary_and_a_secondary_button() -> None:
    html = render(
        "components/hero.html",
        title="Ride better",
        text="Gear from small workshops.",
        url="/shop/",
        action="Shop now",
        secondary_url="/sell/",
        secondary_action="Sell with us",
        heading_level=1,
        image_url="/static/hero.svg",
        image_alt="",
        image_width=1600,
        image_height=900,
    )
    assert html.count("<h1") == 1
    assert "hero__scrim" in html
    assert html.count('class="button') == 2
    assert 'fetchpriority="high"' in html
    assert "carousel" not in html
    assert "autoplay" not in html
    assert "<video" not in html


def test_the_footer_has_three_columns() -> None:
    html = render(
        "components/footer.html",
        site_name="Ride",
        links=[{"label": "About", "url": "/about/"}],
        legal_links=[{"label": "Privacy", "url": "/privacy/"}],
    )
    assert html.count('class="site-footer__col') == 3
    assert 'href="/about/"' in html
    assert 'href="/privacy/"' in html


def test_the_icon_component_draws_from_the_same_origin_sprite() -> None:
    html = render("components/icon.html", name="search")
    assert html.startswith("<svg")
    assert 'aria-hidden="true"' in html
    assert re.search(r'<use href="[^"]*sprite[^"]*\.svg#search"', html)
    assert "http://" not in html
    assert "https://" not in html


def test_a_product_tile_is_not_one_giant_link() -> None:
    html = render(
        "components/product_tile.html",
        name="Touring jacket",
        url="/p/touring-jacket/",
        price="EUR 129.00",
    )
    assert html.count("<a ") == 2  # the name and the small "View" button
    assert "product-tile__link" not in html  # base's class covers the whole tile
    assert re.search(
        r'class="button button--small[^"]*"[^>]*>View<span class="visually-hidden">', html
    )
