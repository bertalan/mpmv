"""THM.4 (3/3): the rest of the `base` component library (spec 14 §14.9, WCAG 2.2 AA).

Each component is one file in `templates/components/`, documented at its top and included with
`only`. The tests pin the accessible structure; the CSP lint and the styleguide tests cover the
rest. Interactive parts (dialog, carousel buttons, quantity stepper, toasts) are rendered
`hidden` or work natively, so every component still works without JavaScript.
"""

import re
from datetime import UTC, datetime
from typing import Any

import pytest
from django.template.loader import render_to_string
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.themes.build import render_themes
from apps.themes.tests.builders import SHIPPED_THEMES


@pytest.fixture(autouse=True)
def _base(settings: SettingsWrapper) -> None:
    settings.MPMV_THEME_DEFAULT = "base"


@pytest.fixture(scope="module")
def css() -> str:
    return render_themes(SHIPPED_THEMES)["themes/base/theme.css"]


@pytest.fixture(scope="module")
def script() -> str:
    return (SHIPPED_THEMES / "base/static/themes/base/js/base.js").read_text(encoding="utf-8")


def render(component: str, /, **context: Any) -> str:
    return render_to_string(f"components/{component}.html", context).strip()


def assert_no_inline_code(html: str) -> None:
    assert "style=" not in html
    assert not re.search(r"\son[a-z]+=", html)
    assert "<script" not in html


# --- Search form


def test_the_search_form_is_a_search_landmark_with_a_labelled_field() -> None:
    html = render("search_form", id="q1", action="/search/")
    assert re.match(r'<form class="search-form" role="search" action="/search/" method="get"', html)
    assert '<label for="q1"' in html
    assert re.search(r'<input type="search" name="q" id="q1"', html)
    assert re.search(r'<button type="submit"[^>]*>', html)
    assert_no_inline_code(html)


def test_the_search_form_keeps_the_query_and_can_hide_its_label() -> None:
    html = render("search_form", id="q2", action="/search/", query="gloves", compact=True)
    assert 'value="gloves"' in html
    assert re.search(r'<label for="q2" class="visually-hidden"', html)


# --- Mini-cart status


def test_the_mini_cart_says_the_item_count_in_words() -> None:
    html = render("mini_cart", url="/cart/", count=3)
    assert re.match(r'<a class="mini-cart" href="/cart/"', html)
    assert "3 items" in html
    assert 'aria-hidden="true"' in html  # the icon and the visual badge


def test_an_empty_mini_cart_says_so() -> None:
    html = render("mini_cart", url="/cart/", count=0)
    assert "empty" in html.lower()
    assert "mini-cart__count" not in html


# --- Price


def test_a_price_shows_the_amount() -> None:
    html = render("price", amount="€ 39.00")
    assert re.match(r'<p class="price">', html)
    assert "€ 39.00" in html


def test_a_reduced_price_strikes_the_old_one_and_shows_the_omnibus_reference() -> None:
    html = render("price", amount="€ 39.00", before="€ 49.00", lowest="€ 45.00")
    assert re.search(r"<del>.*€ 49.00.*</del>", html, re.DOTALL)
    assert re.search(r'<span class="visually-hidden">[^<]+</span>\s*€ 49.00', html)
    assert "Lowest price in the last 30 days" in html
    assert "€ 45.00" in html


def test_a_price_on_request_shows_no_amount() -> None:
    html = render("price", amount="€ 39.00", on_request=True)
    assert "Price on request" in html
    assert "€ 39.00" not in html


# --- Rating


def test_a_rating_says_its_value_in_words_and_hides_the_stars() -> None:
    html = render("rating", value=4.5, count=12)
    assert re.match(r'<p class="rating">', html)
    assert "4.5 out of 5" in html
    assert "12 reviews" in html
    stars = re.search(
        r'<span class="rating__stars" aria-hidden="true">(.*?)<span class="rating__value">',
        html,
        re.S,
    )
    assert stars is not None
    assert stars.group(1).count("rating__star--full") == 4
    assert stars.group(1).count('class="icon') == 5


def test_a_product_without_reviews_says_so() -> None:
    html = render("rating", value=None, count=0)
    assert "No reviews yet" in html
    assert "rating__stars" not in html


# --- Accordion and FAQ


ITEMS = [
    {"title": "Shipping", "content": "Two to four days."},
    {"title": "Returns", "content": "Within 14 days.", "open": True},
]


def test_an_accordion_is_a_list_of_native_disclosures() -> None:
    html = render("accordion", items=ITEMS)
    assert re.match(r'<div class="accordion">', html)
    assert html.count("<details") == 2
    assert html.count("<summary") == 2
    assert html.count('<details class="accordion__item" open>') == 1
    assert_no_inline_code(html)


def test_an_exclusive_accordion_names_its_group() -> None:
    html = render("accordion", items=ITEMS, name="sizes")
    assert html.count('name="sizes"') == 2


def test_a_faq_has_a_heading_and_one_disclosure_per_question() -> None:
    questions = [{"question": "Do you ship abroad?", "answer": "Yes, across the EU."}]
    html = render("faq", id="faq1", title="Questions", questions=questions)
    assert re.match(r'<section class="faq" aria-labelledby="faq1">', html)
    assert '<h2 id="faq1">Questions</h2>' in html
    assert "<summary" in html
    assert "Do you ship abroad?" in html


# --- Dialog and gallery


def test_a_dialog_is_named_by_its_title_and_closes_without_script() -> None:
    html = render("dialog", id="d1", title="Size guide", body="Measure your chest.")
    assert re.search(r'<dialog class="dialog" id="d1" aria-labelledby="d1-title">', html)
    assert '<h2 id="d1-title"' in html
    # A form with method="dialog" closes it natively.
    assert re.search(r'<form method="dialog">', html)
    assert_no_inline_code(html)


def test_a_dialog_trigger_is_hidden_until_the_script_runs() -> None:
    html = render("dialog", id="d2", title="Size guide", body="…", open_label="Open size guide")
    assert re.search(r'<button type="button"[^>]*data-dialog="d2"[^>]*hidden', html)
    assert 'aria-haspopup="dialog"' in html


def test_the_theme_script_opens_dialogs_as_modals(script: str) -> None:
    assert "[data-dialog]" in script
    assert "showModal" in script


IMAGES = [
    {"url": "/static/a.svg", "alt": "Front view"},
    {"url": "/static/a.svg", "alt": "Side view"},
]


def test_a_gallery_lists_its_images_with_a_zoom_dialog_each() -> None:
    html = render("gallery", id="g1", label="Product images", images=IMAGES)
    assert re.match(r'<section class="gallery" aria-label="Product images">', html)
    assert html.count('<dialog class="dialog dialog--image"') == 2
    assert html.count("data-dialog=") == 2
    assert 'alt="Front view"' in html
    assert_no_inline_code(html)


def test_a_gallery_without_images_shows_a_placeholder() -> None:
    html = render("gallery", id="g2", label="Product images", images=[])
    assert "gallery__placeholder" in html
    assert "<dialog" not in html


# --- Carousel


def test_a_carousel_is_a_labelled_scroll_snap_list_with_hidden_buttons() -> None:
    slides = [{"title": "One"}, {"title": "Two"}]
    html = render("carousel", id="c1", label="Featured", slides=slides)
    assert re.match(r'<section class="carousel" aria-roledescription="carousel"', html)
    assert 'aria-label="Featured"' in html
    # The track scrolls with the keyboard too, so it is focusable and named.
    assert re.search(r'<ul class="carousel__track" id="c1-track" tabindex="0"', html)
    assert html.count("<li") == 2
    assert html.count("data-carousel=") == 2
    assert html.count(" hidden") >= 2
    assert_no_inline_code(html)


def test_the_carousel_snaps_and_honours_reduced_motion(css: str, script: str) -> None:
    assert "scroll-snap-type" in css
    assert "[data-carousel]" in script
    assert "prefers-reduced-motion" in script


# --- Quantity stepper, cart line, order summary, step indicator


def test_a_quantity_stepper_is_a_number_field_with_hidden_buttons() -> None:
    html = render("quantity_stepper", id="qty1", label="Quantity", value=2, min=1, max=5)
    assert '<label for="qty1"' in html
    assert re.search(
        r'<input type="number" id="qty1" name="quantity" value="2" min="1" max="5"', html
    )
    assert 'inputmode="numeric"' in html
    assert len(re.findall(r'<button type="button"[^>]*data-step="-?1"[^>]*hidden', html)) == 2
    assert_no_inline_code(html)


def test_a_disabled_quantity_stepper_disables_the_field() -> None:
    html = render("quantity_stepper", id="qty2", label="Quantity", value=1, disabled=True)
    assert re.search(r"<input[^>]*\sdisabled", html)


def test_the_theme_script_steps_quantities(script: str) -> None:
    assert "[data-step]" in script
    assert "stepUp" in script


LINE: dict[str, Any] = {
    "id": "line1",
    "name": "Touring jacket",
    "url": "/p/jacket/",
    "vendor": "Vendor A",
    "unit_price": "€ 189.00",
    "quantity": 1,
    "total": "€ 189.00",
}


def test_a_cart_line_names_the_product_and_its_totals() -> None:
    html = render("cart_line", **LINE)
    assert re.match(r'<li class="cart-line"', html)
    assert '<a href="/p/jacket/">Touring jacket</a>' in html
    assert "€ 189.00" in html
    assert 'id="line1-quantity"' in html
    assert re.search(r'<button type="submit"[^>]*aria-label="Remove Touring jacket"', html)


def test_an_unavailable_cart_line_says_why() -> None:
    html = render("cart_line", **LINE, problem="Only one left.")
    assert "cart-line--problem" in html
    assert "Only one left." in html


def test_an_order_summary_lists_its_lines_and_the_total() -> None:
    lines = [{"label": "Subtotal", "amount": "€ 189.00"}, {"label": "Shipping", "amount": "€ 9"}]
    html = render("order_summary", id="os1", lines=lines, total="€ 198.00", note="VAT included")
    assert re.match(r'<section class="order-summary" aria-labelledby="os1">', html)
    assert html.count("<dt") == 3
    assert re.search(r'<dt class="order-summary__total">', html)
    assert "VAT included" in html


STEPS = [
    {"label": "Cart", "url": "/cart/"},
    {"label": "Address", "url": "/checkout/address/"},
    {"label": "Payment", "url": "/checkout/payment/"},
]


def test_the_step_indicator_marks_done_current_and_upcoming_steps() -> None:
    html = render("step_indicator", steps=STEPS, current=2)
    assert re.match(r'<nav class="steps" aria-label="Checkout steps">', html)
    assert "<ol" in html
    assert '<a href="/cart/">' in html
    assert html.count('aria-current="step"') == 1
    assert '<a href="/checkout/payment/">' not in html
    assert html.count("steps__step--done") == 1
    # Done is said in words, never by colour or a tick alone.
    assert re.search(r'<span class="visually-hidden">[^<]*[Cc]ompleted', html)


# --- Empty state, toasts, table


def test_an_empty_state_has_a_heading_text_and_an_optional_action() -> None:
    html = render("empty_state", title="Your cart is empty", text="Browse.", url="/", action="Shop")
    assert re.match(r'<div class="empty-state">', html)
    assert "<h2>Your cart is empty</h2>" in html
    assert '<a class="button" href="/">Shop</a>' in html
    assert "<a" not in render("empty_state", title="Nothing here")


def test_the_toast_region_is_a_polite_status_region_present_on_load() -> None:
    html = render("toast", id="t1", messages=["Cart updated."])
    assert re.match(r'<div class="toast-region" id="t1" role="status" aria-live="polite">', html)
    assert html.split("<template")[0].count('class="toast"') == 1
    assert "<template" in html
    assert "data-dismiss" in html


def test_the_theme_script_adds_toasts_and_dismisses_them(script: str) -> None:
    assert "[data-toast]" in script
    assert ".toast" in script


def test_a_responsive_table_scrolls_in_a_named_focusable_region() -> None:
    columns = [{"label": "Order"}, {"label": "Total", "numeric": True}]
    rows = [["#1001", "€ 39.00"]]
    html = render("table", id="tb1", caption="Orders", columns=columns, rows=rows)
    assert re.match(
        r'<div class="table-scroll" role="region" aria-labelledby="tb1-caption" tabindex="0">',
        html,
    )
    assert '<caption id="tb1-caption">Orders</caption>' in html
    assert html.count('<th scope="col"') == 2
    assert 'class="numeric"' in html


def test_an_empty_table_says_so_in_one_cell() -> None:
    html = render(
        "table", id="tb2", caption="Orders", columns=[{"label": "A"}], rows=[], empty="None"
    )
    assert re.search(r'<td colspan="1">None</td>', html)


# --- Content blocks: hero, CTA band, logo strip, launch countdown


def test_a_hero_has_its_heading_text_and_actions() -> None:
    html = render("hero", title="Ride further", text="Gear for long trips.", url="/", action="Shop")
    assert re.match(r'<div class="hero', html)
    assert "<h2" in html
    assert '<a class="button button--large" href="/">Shop</a>' in html
    assert "<h1" in render("hero", title="Home", heading_level=1)


def test_a_hero_image_is_decorative_unless_described() -> None:
    html = render("hero", title="Ride", image_url="/static/a.svg")
    assert re.search(r'<img[^>]*alt=""', html)
    assert "hero--image" in html


def test_a_cta_band_has_a_heading_and_one_action() -> None:
    html = render(
        "cta_band", title="Sell with us", text="Open a shop.", url="/sell/", action="Apply"
    )
    assert re.match(r'<div class="cta-band', html)
    assert html.count("<a") == 1


def test_a_logo_strip_names_every_logo() -> None:
    logos = [
        {"name": "Vendor A", "image_url": "/a.svg", "url": "/v/a/"},
        {"name": "Vendor B", "image_url": "/b.svg"},
    ]
    html = render("logo_strip", label="Our vendors", logos=logos)
    assert re.match(r'<ul class="logo-strip" role="list" aria-label="Our vendors">', html)
    assert 'alt="Vendor A"' in html
    assert html.count("<a ") == 1


def test_a_launch_countdown_shows_the_absolute_date_before_and_the_expired_text_after() -> None:
    target = datetime(2026, 12, 1, 9, 0, tzinfo=UTC)
    upcoming = render(
        "launch_countdown", title="New jacket", target=target, url="/p/", action="See it"
    )
    assert '<time datetime="2026-12-01T09:00:00+00:00"' in upcoming
    assert "data-countdown" in upcoming
    assert "aria-live" not in upcoming
    expired = render(
        "launch_countdown",
        title="New jacket",
        target=target,
        expired=True,
        expired_text="Now available",
        expired_url="/p/",
    )
    assert "Now available" in expired
    assert "launch-countdown--expired" in expired


# --- Every new component has its style


@pytest.mark.parametrize(
    "selector",
    [
        ".search-form",
        ".mini-cart",
        ".price",
        ".rating",
        ".accordion",
        ".dialog",
        ".carousel__track",
        ".gallery",
        ".quantity",
        ".cart-line",
        ".order-summary",
        ".steps",
        ".empty-state",
        ".toast",
        ".table-scroll",
        ".hero",
        ".cta-band",
        ".logo-strip",
        ".faq",
        ".launch-countdown",
        ".image-credit",
    ],
)
def test_every_component_has_its_style(css: str, selector: str) -> None:
    assert selector in css
