"""THM.16 (1/2): the `ride` cart and checkout templates use Bootstrap 5.3 classes (spec 14 §14.9).

Rendered with `ride` active and, for the "base is untouched" checks, with `base`. Behaviour and
hooks stay: field names, HTMX attributes, base.js hooks, labels and the error summary links.
The browser checks (axe at 360, 768 and 1280, CSP) are in tests/e2e/test_ride_checkout.py.
"""

import re
from pathlib import Path
from typing import Any

import pytest
from django import forms
from django.template.loader import render_to_string

from apps.themes.services import use_theme
from apps.themes.tests.builders import SHIPPED_THEMES

pytestmark = pytest.mark.usefixtures("english_interface")


def render(theme: str, name: str, /, **context: Any) -> str:
    with use_theme(theme):
        return render_to_string(name, context).strip()


class DetailsForm(forms.Form):
    email = forms.EmailField(label="Email")
    country = forms.ChoiceField(label="Country", choices=[("IT", "Italy")])
    is_business = forms.BooleanField(label="Business", required=False)


PACKAGE: dict[str, Any] = {
    "summary_id": "pkg-1",
    "vendor_id": 1,
    "closure_notice": None,
    "vendor": "Vendor A",
    "messages": ["Stock is low"],
    "quote_url": "",
    "options": [
        {"key": "std", "label": "Standard", "price": "5.00", "delivery": "3 days", "checked": True}
    ],
    "field_name": "ship_1",
    "lines": [
        {
            "id": "7",
            "name": "Helmet",
            "unit_price": "10.00",
            "quantity": 2,
            "total": "20.00",
            "problem": "",
        }
    ],
    "total": "25.00",
    "summary_lines": [{"label": "Items", "amount": "20.00"}],
    "note": "",
}


def test_the_step_indicator_is_a_bootstrap_list_with_badges() -> None:
    html = render(
        "ride",
        "components/step_indicator.html",
        steps=[
            {"label": "Cart", "url": "/cart/"},
            {"label": "Details", "url": ""},
            {"label": "Pay"},
        ],
        current=2,
    )
    assert "<nav" in html
    assert 'aria-label="Checkout steps"' in html
    assert 'aria-current="step"' in html
    assert "badge" in html
    assert "list-unstyled" in html
    assert "d-flex" in html
    assert "(completed)" in html


def test_the_cart_line_is_a_list_group_item_on_the_grid_and_keeps_its_hooks() -> None:
    html = render(
        "ride",
        "components/cart_line.html",
        id="7",
        name="Helmet",
        unit_price="10.00",
        quantity=2,
        total="20.00",
        quantity_name="qty_7",
        problem="Out of stock",
    )
    assert "list-group-item" in html
    assert 'class="row' in html
    assert 'name="remove" value="7"' in html
    assert 'name="qty_7"' in html
    assert "data-step" in html
    assert "form-control" in html
    assert "btn " in html
    assert "<strong>Warning:</strong> Out of stock" in html


def test_the_order_summary_is_a_card_with_the_total_last() -> None:
    html = render(
        "ride",
        "components/order_summary.html",
        id="s",
        lines=[{"label": "Items", "amount": "1.00"}],
        total="1.00",
        note="VAT included",
    )
    assert 'class="order-summary card' in html
    assert "card-body" in html
    assert html.rindex("Total") > html.index("Items")


def test_a_field_uses_bootstrap_controls_a_label_and_a_linked_error() -> None:
    form = DetailsForm({"email": "nope", "country": "IT"})
    assert not form.is_valid()
    html = render("ride", "components/field.html", field=form["email"])
    assert 'class="form-control is-invalid"' in html
    assert re.search(r'<label class="form-label" for="id_email">Email', html)
    assert 'class="invalid-feedback d-block" id="id_email_error"' in html
    assert "(required)" in html
    select = render("ride", "components/field.html", field=form["country"])
    assert "form-select" in select
    box = render("ride", "components/field.html", field=form["is_business"])
    assert 'class="form-check-input"' in box
    assert "form-check-label" in box


def test_the_error_summary_is_a_danger_alert_linked_to_the_fields() -> None:
    form = DetailsForm({"email": "nope", "country": "IT"})
    assert not form.is_valid()
    html = render("ride", "components/error_summary.html", form=form, autofocus=True)
    assert "alert alert-danger" in html
    assert "data-autofocus" in html
    assert 'tabindex="-1"' in html
    assert 'href="#id_email"' in html


def test_the_alert_carries_the_bootstrap_level_class_and_keeps_its_roles() -> None:
    html = render(
        "ride", "components/alert.html", level="warning", message="Careful", live="assertive"
    )
    assert "alert-warning" in html
    assert 'role="alert"' in html
    assert "Warning" in html


def test_the_cart_body_has_bootstrap_buttons_and_keeps_the_htmx_hooks() -> None:
    html = render(
        "ride",
        "orders/_cart_body.html",
        packages=[PACKAGE],
        can_continue=True,
        cart_messages=[],
        order=None,
    )
    assert 'hx-post="' in html
    assert 'hx-target="#cart-body"' in html
    assert 'class="btn btn-outline-secondary"' in html
    assert re.search(
        r'<a class="btn btn-primary btn-lg" href="[^"]+">Continue to checkout</a>', html
    )
    assert "card" in html
    assert "list-group" in html


def test_the_review_package_keeps_the_shipping_radio_hooks() -> None:
    html = render(
        "ride", "orders/_package.html", package=PACKAGE, packages=[PACKAGE], mode="review"
    )
    assert 'class="form-check-input"' in html
    assert 'name="ship_1"' in html
    assert 'hx-target="#review-body"' in html
    assert "<legend" in html


def test_the_review_actions_are_bootstrap_buttons() -> None:
    html = render("ride", "orders/_review_actions.html", placeable=True, pay_label="Place order")
    assert 'class="btn btn-primary btn-lg"' in html
    assert 'id="review-actions"' in html


@pytest.mark.parametrize(
    "name",
    [
        "components/step_indicator.html",
        "components/cart_line.html",
        "components/order_summary.html",
        "components/error_summary.html",
        "components/field.html",
        "components/alert.html",
        "components/quantity_stepper.html",
        "orders/cart.html",
        "orders/_cart_body.html",
        "orders/_package.html",
        "orders/checkout.html",
        "orders/review.html",
        "orders/_review_body.html",
        "orders/_review_actions.html",
        "orders/_legal_boxes.html",
        "orders/_legal_recap.html",
        "orders/confirmation.html",
    ],
)
def test_the_ride_templates_avoid_the_banned_helpers_and_inline_code(name: str) -> None:
    source = (SHIPPED_THEMES / "ride" / "templates" / name).read_text(encoding="utf-8")
    assert not re.search(r"\b(?:text-bg|bg)-(?:info|warning|secondary)\b", source)
    assert not re.search(r"\sstyle\s*=|<style|\son[a-z]+\s*=|<script(?![^>]*\ssrc)", source)


def test_base_renders_its_own_markup_without_bootstrap_classes() -> None:
    form = DetailsForm({"email": "nope", "country": "IT"})
    form.is_valid()
    pages: dict[str, dict[str, Any]] = {
        "components/field.html": {"field": form["email"]},
        "components/step_indicator.html": {"steps": [{"label": "A", "url": "/"}], "current": 1},
        "components/order_summary.html": {"id": "s", "total": "1.00"},
        "components/alert.html": {"level": "info", "message": "x"},
        "components/cart_line.html": {
            "id": "1",
            "name": "n",
            "unit_price": "1",
            "quantity": 1,
            "total": "1",
        },
    }
    for name, context in pages.items():
        html = render("base", name, **context)
        assert not re.search(
            r"\b(?:form-control|list-group-item|card-body|alert-info|rounded-pill)\b", html
        ), name


def test_the_base_templates_are_not_overridden_by_this_task() -> None:
    base = SHIPPED_THEMES / "base" / "templates"
    assert not (base / "orders").exists()
    for name in ("field", "step_indicator", "cart_line", "order_summary", "error_summary"):
        text = (base / "components" / f"{name}.html").read_text(encoding="utf-8")
        assert "form-control" not in text
        assert "list-group" not in text
        assert "card-body" not in text
    assert isinstance(base, Path)


class ChoiceForm(forms.Form):
    kind = forms.ChoiceField(
        label="Kind", choices=[("a", "Alpha"), ("b", "Beta")], widget=forms.RadioSelect
    )
    extras = forms.MultipleChoiceField(
        label="Extras",
        choices=[("x", "Ex"), ("y", "Why")],
        widget=forms.CheckboxSelectMultiple,
    )
    agree = forms.BooleanField(label="Agree")
    doc = forms.FileField(label="Document")
    photo = forms.ImageField(label="Photo", widget=forms.ClearableFileInput)


@pytest.mark.parametrize("name", ["kind", "extras"])
def test_a_choice_group_puts_the_check_class_on_each_input_not_on_the_wrapper(name: str) -> None:
    form = ChoiceForm({})
    html = render("ride", "components/field.html", field=form[name])
    wrapper = re.search(rf'<div id="id_{name}"([^>]*)>', html)
    assert wrapper is not None
    assert "class" not in wrapper.group(1)
    inputs = re.findall(r"<input[^>]*>", html)
    assert len(inputs) == 2
    assert all('class="form-check-input is-invalid"' in tag for tag in inputs)
    assert html.count('class="form-check"') == 2
    assert f'id="id_{name}_0"' in html
    assert f'for="id_{name}_0"' in html


def test_a_valid_choice_group_has_no_invalid_class() -> None:
    form = ChoiceForm({"kind": "a", "extras": ["x"], "agree": "on"})
    html = render("ride", "components/field.html", field=form["kind"])
    assert "is-invalid" not in html
    assert 'class="form-check-input"' in html


def test_a_single_checkbox_with_errors_is_invalid() -> None:
    form = ChoiceForm({})
    html = render("ride", "components/field.html", field=form["agree"])
    assert 'class="form-check-input is-invalid"' in html


@pytest.mark.parametrize("name", ["doc", "photo"])
def test_file_inputs_get_the_form_control_class(name: str) -> None:
    form = ChoiceForm({})
    html = render("ride", "components/field.html", field=form[name])
    assert re.search(r'<input type="file"[^>]*class="form-control is-invalid"', html)


def test_the_field_keeps_the_base_wrapper_classes_beside_bootstrap_ones() -> None:
    form = ChoiceForm({})
    group = render("ride", "components/field.html", field=form["kind"])
    assert re.match(
        r'<div class="[^"]*\bfield\b[^"]*\bfield--group\b[^"]*\bfield--invalid\b', group
    )
    box = render("ride", "components/field.html", field=form["agree"])
    assert re.match(r'<div class="[^"]*\bfield\b[^"]*\bfield--check\b[^"]*\bfield--invalid\b', box)
    ok = render("ride", "components/field.html", field=DetailsForm()["email"])
    assert "field--invalid" not in ok
    assert 'class="field ' in ok


def test_an_error_level_alert_gets_the_danger_title() -> None:
    html = render("ride", "components/alert.html", level="error", message="Broken")
    assert "alert-danger" in html
    assert "alert--error" in html
    assert ">Error<" in html
