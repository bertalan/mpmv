"""THM.4 (2/3): cards, product tiles, badges and alerts of `base` (spec 14 §14.9).

- Cards (`components/card.html`): a titled box; with a `url` the title link covers the whole
  card through a stretched pseudo-element, so the card holds one interactive element only.
- Product tiles (`components/product_tile.html`): image with its alt text, the product name as
  the only link, the price or "Price on request", the vendor and the badges.
- Badges (`components/badge.html`): sale, new, out of stock and on request, each in words
  (never colour alone, WCAG 1.4.1).
- Alerts (`components/alert.html`): info, success, warning and danger with the level in words, a
  dismissible variant whose button needs no inline script, and a live-region variant.
"""

import re
from typing import Any

import pytest
from django.template.loader import render_to_string
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.themes.build import render_themes
from apps.themes.tests.builders import SHIPPED_THEMES

INTERACTIVE = re.compile(r"<(?:a|button|input|select|textarea|details)[\s>]")


@pytest.fixture(autouse=True)
def _base(settings: SettingsWrapper) -> None:
    settings.MPMV_THEME_DEFAULT = "base"


@pytest.fixture(scope="module")
def base_css() -> str:
    return render_themes(SHIPPED_THEMES)["themes/base/theme.css"]


def render(component: str, /, **context: Any) -> str:
    return render_to_string(f"components/{component}.html", context).strip()


def assert_no_inline_code(html: str) -> None:
    assert "style=" not in html
    assert not re.search(r"\son[a-z]+=", html)
    assert "<script" not in html


# --- Cards


def test_a_card_has_a_heading_and_its_text() -> None:
    html = render("card", title="Spring service", text="Book a check before the season.")
    assert html.startswith('<article class="card"')
    assert re.search(r"<h3 class=\"card__title\">\s*Spring service\s*</h3>", html)
    assert "Book a check before the season." in html
    assert not INTERACTIVE.search(html)


def test_a_card_heading_level_can_be_changed() -> None:
    html = render("card", title="Spring service", heading_level=2)
    assert re.search(r"<h2 class=\"card__title\">", html)


def test_a_linked_card_has_one_link_on_its_title() -> None:
    html = render("card", title="Spring service", url="/service/", text="Details inside.")
    assert "card--linked" in html
    assert len(INTERACTIVE.findall(html)) == 1
    assert re.search(r'<a class="card__link" href="/service/">\s*Spring service\s*</a>', html)


def test_a_card_image_carries_its_alt_text() -> None:
    html = render("card", title="Workshop", image_url="/img/w.svg", image_alt="A bright workshop")
    assert re.search(r'<img[^>]*src="/img/w.svg"[^>]*alt="A bright workshop"', html)


def test_a_card_never_uses_inline_code() -> None:
    assert_no_inline_code(render("card", title="T", url="/", image_url="/i.svg", image_alt="I"))


# --- Badges


@pytest.mark.parametrize(
    ("kind", "text"),
    [
        ("sale", "Sale"),
        ("new", "New"),
        ("out_of_stock", "Out of stock"),
        ("on_request", "On request"),
    ],
)
def test_each_badge_says_its_meaning_in_words(kind: str, text: str) -> None:
    html = render("badge", kind=kind)
    modifier = kind.replace("_", "-")
    assert re.fullmatch(rf'<span class="badge badge--{modifier}">\s*{text}\s*</span>', html), html


def test_a_badge_label_can_be_replaced() -> None:
    assert ">-20%<" in render("badge", kind="sale", label="-20%").replace(" ", "")


@pytest.mark.parametrize("kind", ["sale", "new", "out-of-stock", "on-request"])
def test_each_badge_has_its_style(base_css: str, kind: str) -> None:
    assert f".badge--{kind}" in base_css


# --- Product tiles


def tile(**context: Any) -> str:
    return render(
        "product_tile",
        **{
            "name": "Touring jacket",
            "url": "/p/touring-jacket/",
            "image_url": "/static/sample.svg",
            "image_alt": "A black jacket with reflective stripes",
            "price": "€ 189.00",
            "vendor": "Vendor A",
            **context,
        },
    )


def test_a_tile_links_the_product_name_only() -> None:
    html = tile()
    assert html.startswith('<article class="product-tile"')
    assert len(INTERACTIVE.findall(html)) == 1
    assert re.search(
        r'<h3 class="product-tile__name"><a class="product-tile__link" '
        r'href="/p/touring-jacket/">\s*Touring jacket\s*</a></h3>',
        html,
    ), html


def test_a_tile_image_has_its_alt_text_size_and_lazy_loading() -> None:
    img = re.search(r"<img[^>]*>", tile())
    assert img is not None
    tag = img.group(0)
    assert 'alt="A black jacket with reflective stripes"' in tag
    assert 'loading="lazy"' in tag
    assert re.search(r'width="\d+"', tag)
    assert re.search(r'height="\d+"', tag)


def test_a_tile_image_without_alt_text_is_decorative() -> None:
    img = re.search(r"<img[^>]*>", tile(image_alt=""))
    assert img is not None
    assert 'alt=""' in img.group(0)


def test_a_tile_without_an_image_shows_a_placeholder_hidden_from_assistive_technology() -> None:
    html = tile(image_url="")
    assert "<img" not in html
    assert re.search(r'class="product-tile__placeholder" aria-hidden="true"', html)


def test_a_tile_shows_the_price_and_the_vendor() -> None:
    html = tile()
    assert re.search(r'class="product-tile__price"[^>]*>.*€ 189.00', html, re.DOTALL)
    assert re.search(r'class="product-tile__vendor">\s*Sold by Vendor A\s*<', html)


def test_a_tile_without_a_price_says_price_on_request() -> None:
    html = tile(price="")
    assert re.search(r'class="product-tile__price"[^>]*>\s*Price on request\s*<', html), html


def test_a_sale_tile_shows_the_previous_price_struck_through_and_in_words() -> None:
    html = tile(price="€ 149.00", price_before="€ 189.00", badges=["sale"])
    assert re.search(r'<del><span class="visually-hidden">\s*Was\s*</span>\s*€ 189.00</del>', html)
    assert "badge--sale" in html


def test_a_tile_lists_its_badges() -> None:
    html = tile(badges=["new", "out_of_stock"])
    assert re.search(r'<ul class="product-tile__badges"', html)
    assert html.count("<li>") == 2
    assert "badge--new" in html
    assert "badge--out-of-stock" in html


def test_an_out_of_stock_tile_is_marked() -> None:
    assert 'class="product-tile product-tile--unavailable"' in tile(badges=["out_of_stock"])


def test_a_tile_never_uses_inline_code() -> None:
    assert_no_inline_code(tile(badges=["sale", "new"], price_before="€ 1"))


def test_the_tile_link_covers_the_whole_tile(base_css: str) -> None:
    css = re.sub(r"\s+", "", base_css)
    assert ".product-tile{" in css
    assert re.search(r"\.product-tile[,{][^}]*position:relative", css)
    assert re.search(r"\.product-tile__link::after\{[^}]*content:\"\"[^}]*inset:0", css), css
    assert re.search(r"\.card__link::after[^{]*\{[^}]*inset:0", css)


# --- Alerts


@pytest.mark.parametrize(
    ("level", "word"),
    [("info", "Information"), ("success", "Success"), ("warning", "Warning"), ("danger", "Error")],
)
def test_each_alert_level_is_said_in_words(level: str, word: str) -> None:
    html = render("alert", level=level, message="Your order was saved.")
    assert f"alert alert--{level}" in html
    assert re.search(rf'<p class="alert__title">\s*{word}\s*</p>', html), html
    assert "Your order was saved." in html
    assert "role=" not in html


def test_an_alert_title_can_be_replaced() -> None:
    html = render("alert", level="success", title="Order placed", message="See your email.")
    assert re.search(r'<p class="alert__title">\s*Order placed\s*</p>', html)


@pytest.mark.parametrize("level", ["info", "success", "warning", "danger"])
def test_each_alert_level_has_its_style(base_css: str, level: str) -> None:
    assert f".alert--{level}" in base_css


def test_a_polite_live_alert_is_a_status_region() -> None:
    html = render("alert", level="success", message="Saved.", live="polite")
    assert 'role="status"' in html
    assert "alert--live" in html


def test_an_assertive_live_alert_is_an_alert_region() -> None:
    html = render("alert", level="danger", message="Payment failed.", live="assertive")
    assert 'role="alert"' in html


def test_a_dismissible_alert_has_a_named_button_hidden_until_the_script_runs() -> None:
    html = render("alert", level="info", message="New terms.", dismissible=True)
    assert "alert--dismissible" in html
    button = re.search(r"<button[^>]*>", html)
    assert button is not None
    tag = button.group(0)
    assert 'type="button"' in tag
    assert 'aria-label="Dismiss"' in tag
    assert "data-dismiss" in tag
    assert re.search(r"\shidden[\s>]", tag)
    assert_no_inline_code(html)


def test_the_theme_script_dismisses_alerts_without_inline_handlers() -> None:
    module = render_themes(SHIPPED_THEMES)["themes/base/js/base.js"]
    assert "[data-dismiss]" in module
