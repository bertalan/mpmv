"""THM.27 (1/2): the `ride` cart page is two columns, packages left and a sticky summary right."""

import re
from typing import Any

import pytest
from django.template.loader import render_to_string

from apps.themes.services import use_theme

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("english_interface")]

LINE: dict[str, Any] = {
    "id": "7",
    "name": "Helmet",
    "unit_price": "10.00",
    "quantity": 2,
    "total": "20.00",
    "problem": "",
}

PACKAGE: dict[str, Any] = {
    "summary_id": "pkg-1",
    "vendor": "Vendor A",
    "messages": [],
    "quote_url": "",
    "options": [],
    "field_name": "ship_1",
    "lines": [LINE],
    "total": "25.00",
    "summary_lines": [],
    "note": "",
}

ORDER: dict[str, Any] = {
    "lines": [{"label": "Items", "amount": "20.00"}, {"label": "VAT 22%", "amount": "4.50"}],
    "total": "25.00",
    "note": "",
}


def cart(**extra: Any) -> str:
    context: dict[str, Any] = {
        "packages": [PACKAGE],
        "order": ORDER,
        "can_continue": True,
        "cart_messages": [],
        "empty": False,
        "partial": False,
    }
    context.update(extra)
    with use_theme("ride"):
        return render_to_string("orders/_cart_body.html", context)


def test_the_cart_is_a_row_with_the_packages_left_and_the_summary_right() -> None:
    html = cart()
    assert re.search(r'class="[^"]*\bcart-layout\b[^"]*\brow\b', html)
    assert html.index("<form") < html.index('<aside class="cart-summary'), "tab order"
    assert re.search(r'<div class="[^"]*\bcol-lg-8\b[^"]*">\s*<form', html)
    assert re.search(r'<aside class="cart-summary[^"]*\bcol-lg-4\b', html)


def test_the_summary_landmark_has_a_name() -> None:
    assert re.search(r'<aside class="cart-summary[^"]*" aria-label="Order summary">', cart())


def test_the_summary_holds_the_totals_and_the_continue_link_outside_the_form() -> None:
    html = cart()
    aside = html[html.index('<aside class="cart-summary') :]
    assert 'id="order-summary"' in aside
    assert "Continue to checkout" in aside
    assert "Update cart" not in aside
    assert html.index("</form>") < html.index("<aside")


def test_the_amounts_are_unchanged() -> None:
    html = cart()
    for text in ("20.00", "25.00", "VAT 22%", "Order total"):
        assert text in html


def test_a_line_with_an_image_shows_a_thumbnail() -> None:
    line = {**LINE, "image_url": "/media/h.jpg", "image_alt": "Helmet"}
    html = cart(packages=[{**PACKAGE, "lines": [line]}])
    assert 'src="/media/h.jpg"' in html


def test_the_hint_replaces_the_link_when_the_cart_has_problems() -> None:
    html = cart(can_continue=False)
    assert "Solve the points above to go on." in html
    assert "Continue to checkout" not in html


def test_the_empty_cart_has_no_columns() -> None:
    html = cart(empty=True, packages=[], order=None)
    assert "cart-layout" not in html
