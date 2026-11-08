"""THM.4 (1/3): the form and button components of `base` (spec 14 §14.6, §14.9).

- Buttons (`components/button.html`): primary, secondary, ghost, link and danger variants, three
  sizes, a `<button>` or a link, disabled and loading states, and an icon-only button whose
  accessible name is the label (WCAG 1.1.1, 2.5.8, 4.1.2).
- Form fields (`components/field.html`): text, textarea, select, file, a single checkbox with
  its label after the box, radio and checkbox groups in a fieldset with a legend, disabled
  controls (WCAG 1.3.1, 3.3.2).
- Error summary (`components/error_summary.html`): at the top of the form, one link per field
  error to the control it belongs to, the form's own errors as text (WCAG 3.3.1, 3.3.3).
"""

import re
from typing import Any

import pytest
from django import forms
from django.template.loader import render_to_string
from pytest_django.fixtures import Settings as SettingsWrapper

from apps.themes.build import render_themes
from apps.themes.tests.builders import SHIPPED_THEMES


@pytest.fixture(autouse=True)
def _base(settings: SettingsWrapper) -> None:
    settings.MPMV_THEME_DEFAULT = "base"


# --- Buttons


def button(**context: Any) -> str:
    return render_to_string("components/button.html", {"label": "Save", **context}).strip()


def test_the_default_button_is_a_primary_button_of_type_button() -> None:
    html = button()
    assert re.fullmatch(r'<button type="button" class="button">\s*Save\s*</button>', html), html


def test_a_submit_button_has_type_submit_and_a_name_and_value() -> None:
    html = button(type="submit", name="action", value="save")
    assert 'type="submit"' in html
    assert 'name="action"' in html
    assert 'value="save"' in html


@pytest.mark.parametrize("variant", ["secondary", "ghost", "link", "danger"])
def test_each_variant_adds_its_modifier_class(variant: str) -> None:
    assert f'class="button button--{variant}"' in button(variant=variant)


@pytest.mark.parametrize("size", ["small", "large"])
def test_each_size_adds_its_modifier_class(size: str) -> None:
    assert f'class="button button--{size}"' in button(size=size)


def test_a_button_with_a_url_is_a_link() -> None:
    html = button(url="/cart/", variant="secondary")
    assert re.fullmatch(
        r'<a class="button button--secondary" href="/cart/">\s*Save\s*</a>', html
    ), html


def test_a_disabled_button_carries_the_disabled_attribute() -> None:
    html = button(disabled=True)
    assert re.search(r"<button[^>]*\sdisabled[\s>]", html)


def test_a_disabled_link_has_no_href_and_is_marked_disabled() -> None:
    html = button(url="/cart/", disabled=True)
    assert "href=" not in html
    assert 'aria-disabled="true"' in html
    assert html.startswith("<a ")


def test_a_loading_button_is_busy_disabled_and_says_so_in_words() -> None:
    html = button(loading=True, loading_label="Saving")
    assert 'aria-busy="true"' in html
    assert 'aria-disabled="true"' in html
    assert "button--loading" in html
    assert re.search(r'<span class="button__spinner" aria-hidden="true"></span>', html)
    assert "Saving" in html


def test_a_loading_button_has_a_default_loading_text() -> None:
    html = button(loading=True)
    assert re.search(r'<span class="visually-hidden">\s*Loading\s*</span>', html)


def test_an_icon_button_is_named_by_its_label_and_hides_the_icon() -> None:
    html = button(label="Close", icon="close", icon_only=True)
    assert 'aria-label="Close"' in html
    assert "button--icon" in html
    svg = re.search(r"<svg[^>]*>", html)
    assert svg is not None
    assert 'aria-hidden="true"' in svg.group(0)
    assert 'focusable="false"' in svg.group(0)
    # No visible text next to the icon: the name comes from aria-label only.
    assert ">Close<" not in html.replace(" ", "")


def test_a_button_with_an_icon_and_text_keeps_the_text() -> None:
    html = button(label="Search", icon="search")
    assert "aria-label" not in html
    assert "<svg" in html
    assert "Search" in html


@pytest.mark.parametrize("icon", ["close", "search", "plus", "minus", "menu", "check"])
def test_every_listed_icon_draws_a_path(icon: str) -> None:
    assert re.search(r"<svg[^>]*>\s*<path d=\"[^\"]+\"", button(icon=icon))


def test_an_icon_button_that_is_loading_says_so_in_its_accessible_name() -> None:
    # aria-label replaces the content, so the hidden loading text would never be read.
    html = button(label="Refresh", icon="close", icon_only=True, loading=True, loading_label="Busy")
    assert 'aria-label="Refresh, Busy"' in html
    assert 'class="visually-hidden"' not in html
    assert 'aria-label="Close, Loading"' in button(
        label="Close", icon="close", icon_only=True, loading=True
    )


def test_the_theme_script_stops_busy_and_disabled_buttons_from_acting() -> None:
    module = render_themes(SHIPPED_THEMES)["themes/base/js/base.js"]
    assert '[aria-disabled="true"]' in module
    assert "event.submitter" in module


def test_the_button_never_uses_inline_styles_or_handlers() -> None:
    html = button(loading=True, icon="close", icon_only=True)
    assert "style=" not in html
    assert not re.search(r"\son[a-z]+=", html)


# --- Form fields


class Controls(forms.Form):
    name = forms.CharField(label="Name")
    message = forms.CharField(label="Message", widget=forms.Textarea, required=False)
    country = forms.ChoiceField(label="Country", choices=[("", "Choose"), ("at", "Austria")])
    terms = forms.BooleanField(label="I accept the terms")
    delivery = forms.ChoiceField(
        label="Delivery",
        choices=[("std", "Standard"), ("exp", "Express")],
        widget=forms.RadioSelect,
    )
    extras = forms.MultipleChoiceField(
        label="Extras",
        choices=[("gift", "Gift wrap"), ("note", "Card")],
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )
    receipt = forms.FileField(label="Receipt", required=False)


def field(form: forms.Form, name: str) -> str:
    return render_to_string("components/field.html", {"field": form[name]})


def test_a_textarea_has_a_visible_label_tied_to_it() -> None:
    html = field(Controls(), "message")
    assert '<label for="id_message">' in html
    assert re.search(r'<textarea[^>]*id="id_message"', html)


def test_a_select_has_a_visible_label_tied_to_it() -> None:
    html = field(Controls(), "country")
    assert '<label for="id_country">' in html
    assert re.search(r'<select[^>]*id="id_country"', html)


def test_a_file_input_has_a_visible_label_tied_to_it() -> None:
    html = field(Controls(), "receipt")
    assert '<label for="id_receipt">' in html
    assert re.search(r'<input type="file"[^>]*id="id_receipt"', html)


def test_a_single_checkbox_comes_before_its_label() -> None:
    html = field(Controls(), "terms")
    assert 'class="field field--check"' in html
    box = html.index('id="id_terms"')
    label = html.index('<label for="id_terms">')
    assert box < label


def test_a_radio_group_is_a_fieldset_with_a_legend() -> None:
    html = field(Controls(), "delivery")
    assert re.search(r"<fieldset[^>]*>\s*<legend>Delivery", html)
    assert 'class="field field--group"' in html
    assert html.count('type="radio"') == 2


def test_a_checkbox_group_is_a_fieldset_with_a_legend() -> None:
    html = field(Controls(), "extras")
    assert re.search(r"<fieldset[^>]*>\s*<legend>Extras", html)
    assert html.count('type="checkbox"') == 2


def test_a_disabled_field_is_marked_on_the_wrapper_and_the_control() -> None:
    form = Controls()
    form.fields["name"].disabled = True
    html = field(form, "name")
    assert 'class="field field--disabled"' in html
    assert re.search(r"<input[^>]*\sdisabled[\s>]", html)


def test_an_invalid_checkbox_ties_its_error_to_the_control() -> None:
    form = Controls(data={})
    assert not form.is_valid()
    html = field(form, "terms")
    assert "field--invalid" in html
    assert 'id="id_terms_error"' in html
    control = re.search(r'<input type="checkbox"[^>]*>', html)
    assert control is not None
    assert 'aria-invalid="true"' in control.group(0)


# --- Error summary


def summary(form: forms.Form, **context: Any) -> str:
    return render_to_string("components/error_summary.html", {"form": form, **context})


class Signup(forms.Form):
    email = forms.EmailField(label="Email")
    plan = forms.ChoiceField(
        label="Plan", choices=[("a", "Basic"), ("b", "Plus")], widget=forms.RadioSelect
    )
    note = forms.CharField(label="Note", required=False)

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        if data.get("note") == "clash":
            raise forms.ValidationError("These details clash with an existing account.")
        return data


def test_a_valid_or_unbound_form_has_no_summary() -> None:
    assert summary(Signup()).strip() == ""
    form = Signup(data={"email": "a@example.com", "plan": "a"})
    assert form.is_valid()
    assert summary(form).strip() == ""


def test_the_summary_links_every_field_error_to_its_control() -> None:
    form = Signup(data={"email": "nope"})
    assert not form.is_valid()
    html = summary(form)
    assert re.search(r'<div class="error-summary" id="error-summary" tabindex="-1"', html)
    # A named region: aria-labelledby alone gives a plain div no role to name (ARIA 1.2).
    assert re.search(r'<div class="error-summary"[^>]*\srole="region"', html)
    assert re.search(r'aria-labelledby="error-summary-title"', html)
    assert "data-autofocus" not in html
    assert re.search(r'<h2 id="error-summary-title">[^<]+</h2>', html)
    assert re.search(r'<a href="#id_email">Email: [^<]+</a>', html)


def test_a_group_error_links_to_the_first_option() -> None:
    form = Signup(data={"email": "a@example.com"})
    assert not form.is_valid()
    assert re.search(r'<a href="#id_plan_0">Plan: [^<]+</a>', summary(form))


def test_the_summary_can_ask_for_the_focus_on_load() -> None:
    form = Signup(data={"email": "nope"})
    assert not form.is_valid()
    html = summary(form, autofocus=True)
    assert re.search(r'<div class="error-summary"[^>]*\sdata-autofocus[\s>]', html)
    module = render_themes(SHIPPED_THEMES)["themes/base/js/base.js"]
    assert ".error-summary[data-autofocus]" in module


def test_form_level_errors_are_listed_as_text() -> None:
    form = Signup(data={"email": "a@example.com", "plan": "a", "note": "clash"})
    assert not form.is_valid()
    html = summary(form)
    assert "<li>These details clash with an existing account.</li>" in html


def test_the_summary_id_can_be_changed_for_a_second_form() -> None:
    form = Signup(data={"email": "nope"}, prefix="other")
    html = summary(form, summary_id="other-errors")
    assert 'id="other-errors"' in html
    assert 'aria-labelledby="other-errors-title"' in html
    assert '<a href="#id_other-email">' in html
