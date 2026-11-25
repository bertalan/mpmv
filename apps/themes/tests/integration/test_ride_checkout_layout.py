"""THM.27 (2/2): the `ride` review step is two columns with the order summary beside the
packages, shipping options are selectable tiles over native radios, and the sign-in is a centred
card with the show-password toggle inside the field."""

import re
from typing import Any

import pytest
from django.template.loader import render_to_string
from django.test import RequestFactory

from apps.themes.services import use_theme

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("english_interface")]


@pytest.fixture(autouse=True)
def _ride(settings: Any) -> None:
    settings.MPMV_THEME_DEFAULT = "ride"


PACKAGE: dict[str, Any] = {
    "summary_id": "pkg-1",
    "vendor": "Vendor A",
    "messages": [],
    "quote_url": "",
    "options": [
        {"key": "std", "label": "Standard", "price": "9.90", "delivery": "2 days", "checked": True},
        {"key": "exp", "label": "Express", "price": "19.90", "delivery": "", "checked": False},
    ],
    "field_name": "ship_1",
    "lines": [
        {"id": "7", "name": "Helmet", "unit_price": "10.00", "quantity": 2, "total": "20.00"}
    ],
    "total": "25.00",
    "summary_lines": [],
    "note": "",
}
ORDER: dict[str, Any] = {
    "lines": [{"label": "Items", "amount": "20.00"}],
    "total": "29.90",
    "note": "",
}


def review(**extra: Any) -> str:
    context: dict[str, Any] = {
        "steps": [{"label": "Cart", "url": "/cart/"}, {"label": "Review", "url": ""}],
        "current_step": 2,
        "packages": [PACKAGE],
        "order": ORDER,
        "address": {"name": "A B", "line1": "1 Via", "postcode": "1", "city": "C", "country": "IT"},
        "email": "a@example.com",
        "errors": [],
        "cart_messages": [],
        "placeable": True,
        "pay_label": "Place order",
        "partial": False,
    }
    context.update(extra)
    with use_theme("ride"):
        return render_to_string("orders/review.html", context, request=RequestFactory().get("/"))


def test_the_summary_sits_beside_the_packages_and_the_actions_follow_the_row() -> None:
    html = review()
    assert re.search(r'class="[^"]*\bcheckout-layout\b[^"]*\brow\b', html)
    assert re.search(
        r'<aside id="review-summary" class="cart-summary[^"]*\bcol-lg-4\b[^"]*"'
        r' aria-label="Order summary"',
        html,
    )
    assert html.index("review-body") < html.index('<aside id="review-summary')
    assert html.index('<aside id="review-summary') < html.index('id="review-actions"')
    assert html.count('id="order-summary"') == 1
    assert "29.90" in html[html.index("<aside") :]


def test_a_shipping_change_swaps_the_summary_out_of_band() -> None:
    with use_theme("ride"):
        html = render_to_string(
            "orders/_review_body.html",
            {"packages": [PACKAGE], "order": ORDER, "partial": True, "errors": [], "address": {}},
        )
    assert re.search(r'id="review-summary"[^>]*hx-swap-oob="true"', html) or re.search(
        r'hx-swap-oob="true"[^>]*id="review-summary"', html
    )
    assert "29.90" in html


def test_shipping_options_are_tiles_over_native_radios() -> None:
    html = review()
    assert len(re.findall(r'<input type="radio" class="form-check-input"', html)) == 2
    assert html.count("shipping-tile") >= 2
    assert re.search(r'<label class="shipping-tile[^"]*"', html)
    assert "hx-post=" in html
    assert "checked" in html


def test_the_sign_in_is_a_centred_card_on_an_image_stage() -> None:
    with use_theme("ride"):
        html = render_to_string(
            "manager/login.html", {"form": None, "next": ""}, request=RequestFactory().get("/")
        )
    assert "signin-stage" in html
    assert "data-password-toggle=" in html
    assert re.search(r'class="[^"]*\bsignin-field\b', html)
