"""THM.6: the template overrides of `ride` (spec 14 §14.6, §14.9): header, hero, footer, icons.

Rendered with the `ride` theme active, without a request: the shell parts are pure templates.
"""

import re
from types import SimpleNamespace
from typing import Any

import pytest
from django.template.loader import render_to_string
from django.urls import reverse

from apps.themes.services import use_theme

pytestmark = pytest.mark.usefixtures("english_interface")


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


def test_the_header_is_a_bootstrap_navbar_with_one_switch_point_in_ride_css() -> None:
    html = render("components/header.html", site_name="Ride", links=LINKS)
    assert re.search(r'class="[^"]*\bnavbar\b', html)
    assert "navbar-expand" not in html  # Bootstrap's lg would switch at 62rem, not 64rem
    assert 'data-bs-theme="dark"' in html
    assert "btn-close-white" not in html
    assert 'class="site-name' in html


def test_a_submenu_is_a_bootstrap_dropdown_button_with_aria_expanded_never_hover_only() -> None:
    html = render("components/header.html", site_name="Ride", links=LINKS)
    buttons = re.findall(r"<button[^>]*>", html)
    sub = [b for b in buttons if 'data-bs-toggle="dropdown"' in b]
    assert len(sub) == 1
    assert 'aria-expanded="false"' in sub[0]
    assert 'type="button"' in sub[0]
    assert 'aria-controls="subnav-2"' in sub[0]
    assert 'id="subnav-2"' in html
    assert 'class="dropdown-menu' in html
    # the parent link stays reachable, and the children are plain links in a list
    assert 'href="/vendors/"' in html
    assert 'href="/sell/"' in html
    assert "onmouseover" not in html


def test_the_burger_button_opens_the_offcanvas_menu() -> None:
    html = render("components/header.html", site_name="Ride", links=LINKS)
    toggle = re.search(r'<button[^>]*class="nav-toggle[^>]*>.*?</button>', html, re.S)
    assert toggle is not None
    assert 'data-bs-toggle="offcanvas"' in toggle.group(0)
    assert 'data-bs-target="#site-offcanvas"' in toggle.group(0)
    assert 'aria-controls="site-offcanvas"' in toggle.group(0)
    assert "Menu" in toggle.group(0)
    panel = re.search(r'<div[^>]*id="site-offcanvas"[^>]*>', html)
    assert panel is not None
    assert "offcanvas" in panel.group(0)
    assert 'tabindex="-1"' in panel.group(0)
    assert 'aria-labelledby="site-offcanvas-title"' in panel.group(0)
    assert 'id="site-offcanvas-title"' in html
    close = re.search(r'<button[^>]*data-bs-dismiss="offcanvas"[^>]*>', html)
    assert close is not None
    assert "aria-label=" in close.group(0)
    # the nav lives inside the panel; no Alpine directive, no inline script or style
    assert html.index('id="site-offcanvas"') < html.index("<nav")
    assert "x-data" not in html
    assert "<script" not in html
    assert " style=" not in html


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


def test_the_header_has_no_native_dialog_markup() -> None:
    html = render("components/header.html", site_name="Ride", links=LINKS)
    assert "<dialog" not in html


def test_the_search_link_is_a_modal_trigger_that_still_leads_to_the_search_page() -> None:
    html = render("components/header.html", site_name="Ride", links=LINKS, search_url="/search/")
    trigger = re.search(r'<a[^>]*href="/search/"[^>]*>', html)
    assert trigger
    assert 'data-bs-toggle="modal"' in trigger.group(0)
    assert 'data-bs-target="#search-modal"' in trigger.group(0)
    modal = re.search(r'<div[^>]*id="search-modal"[^>]*>', html)
    assert modal
    assert 'class="modal' in modal.group(0)
    assert 'aria-labelledby="search-modal-title"' in modal.group(0)
    form = re.search(r'<form[^>]*role="search"[^>]*>', html)
    assert form
    assert 'action="/search/"' in form.group(0)
    assert 'name="q"' in html
    assert re.search(r'<button[^>]*data-bs-dismiss="modal"[^>]*aria-label="Close"', html)


def test_the_cart_link_opens_an_offcanvas_with_a_live_region_count_and_an_htmx_body() -> None:
    html = render("components/header.html", site_name="Ride", links=LINKS, cart_url="/cart/")
    trigger = re.search(r'<a[^>]*href="/cart/"[^>]*>', html)
    assert trigger
    assert 'data-bs-toggle="offcanvas"' in trigger.group(0)
    assert 'data-bs-target="#mini-cart-offcanvas"' in trigger.group(0)
    panel = re.search(r'<div[^>]*id="mini-cart-offcanvas"[^>]*>', html)
    assert panel
    assert "offcanvas-end" in panel.group(0)
    assert 'aria-labelledby="mini-cart-title"' in panel.group(0)
    body = re.search(r'<div[^>]*id="mini-cart-body"[^>]*>', html)
    assert body
    assert f'hx-get="{reverse("orders:cart_mini")}"' in body.group(0)  # language prefix aware
    assert "hx-select" not in body.group(0)
    assert re.search(r'<span[^>]*id="cart-count"[^>]*aria-live="polite"', html)
    assert re.search(r'<a[^>]*href="/cart/"[^>]*>[^<]*View cart', html)


def test_the_cart_body_says_how_many_items_it_holds() -> None:
    empty = render("orders/_cart_body.html", empty=True)
    assert 'data-cart-count="0"' in empty
    assert "Your cart is empty" in empty
    assert 'data-cart-label="Your cart is empty"' in empty


def test_the_item_count_adds_the_quantities_of_every_line_of_every_package() -> None:
    from apps.themes.templatetags.theme_tags import cart_item_count

    packages = [
        SimpleNamespace(lines=[SimpleNamespace(quantity=3), SimpleNamespace(quantity=1)]),
        SimpleNamespace(lines=[SimpleNamespace(quantity=2)]),
    ]
    assert cart_item_count(packages) == 6
    assert cart_item_count("") == 0
    assert cart_item_count(None) == 0


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


def test_the_mini_cart_fragment_is_a_bootstrap_list_with_the_count() -> None:
    line = SimpleNamespace(name="Blue vase", variant="", quantity=2, total="\u20ac 20.00")
    html = render("orders/_cart_mini.html", lines=[line], items=2, cart_url="/cart/")
    assert 'data-cart-count="2"' in html
    assert 'data-cart-label="2 items"' in html
    assert "list-group" in html
    assert "Blue vase" in html
    assert "20.00" in html
    assert "<form" not in html  # the "View cart" link is the panel's own, in the header
    assert "Shipping" not in html


def test_the_empty_mini_cart_fragment_says_so() -> None:
    html = render("orders/_cart_mini.html", lines=[], items=0, cart_url="/cart/")
    assert 'data-cart-count="0"' in html
    assert "Your cart is empty" in html


def test_the_mini_cart_request_is_background_and_skipped_on_sign_in_pages() -> None:
    """The mini-cart's htmx request is not user activity (X-MPMV-Poll) and never runs on the
    sign-in, second-factor or passkey pages, where it could race the session write of a login."""
    from pathlib import Path

    header = (
        Path(__file__).resolve().parents[4] / "themes/ride/templates/components/header.html"
    ).read_text(encoding="utf-8")
    assert 'hx-headers=\'{"X-MPMV-Poll": "1"}\'' in header
    for guard in ('app_name != "mfa"', 'app_name != "levels"', '"login" not in page_path'):
        assert header.count(guard) == 2, guard  # the trigger link and the offcanvas agree
