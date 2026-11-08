"""THM.3: the footer and form field components of `base` (spec 14 §14.6, WCAG 2.2 AA).

- Footer: the footer menu (CMS.1), a legal links slot (filled by CMS.5), the help link in the
  same place on every page (3.2.6) and labelled navigation landmarks (1.3.1).
- Form field: a visible label tied to the control, a visible required marker, help text and
  inline errors tied to the control with `aria-describedby` and `aria-invalid` (1.3.1, 3.3.1,
  3.3.2).
"""

import re
from typing import Any

import pytest
from django import forms
from django.template.loader import render_to_string
from pytest_django.fixtures import Settings as SettingsWrapper


@pytest.fixture(autouse=True)
def _base(settings: SettingsWrapper) -> None:
    settings.MPMV_THEME_DEFAULT = "base"


def footer(**context: Any) -> str:
    return render_to_string("components/footer.html", {"site_name": "Shop", **context})


def test_the_footer_lists_the_footer_menu_in_a_labelled_nav() -> None:
    html = footer(links=[{"label": "About us", "url": "/about/"}])
    nav = re.search(r'<nav aria-label="Footer">(.*?)</nav>', html, re.DOTALL)
    assert nav is not None
    assert '<a href="/about/">About us</a>' in nav.group(1)


def test_the_footer_has_a_legal_links_slot() -> None:
    html = footer(legal_links=[{"label": "Terms", "url": "/terms/"}])
    nav = re.search(r'<nav aria-label="Legal">(.*?)</nav>', html, re.DOTALL)
    assert nav is not None
    assert '<a href="/terms/">Terms</a>' in nav.group(1)


def test_an_empty_footer_renders_no_empty_navigation() -> None:
    html = footer()
    assert "<nav" not in html
    assert '<footer class="site-footer">' in html
    assert "Shop" in html


def test_the_footer_carries_the_help_link() -> None:
    assert '<a class="help-link" href="/help/">' in footer(help_url="/help/")


class SampleForm(forms.Form):
    email = forms.EmailField(label="Email", help_text="We never share it.")
    note = forms.CharField(label="Note", required=False)


def field(form: forms.Form, name: str) -> str:
    return render_to_string("components/field.html", {"field": form[name]})


def test_the_label_is_visible_and_tied_to_the_control() -> None:
    html = field(SampleForm(), "email")
    assert re.search(r'<label for="id_email"[^>]*>', html)
    assert re.search(r'<input[^>]*id="id_email"', html)
    assert "placeholder" not in html


def test_required_fields_are_marked_with_text_and_the_attribute() -> None:
    html = field(SampleForm(), "email")
    marker = re.search(r'<span class="field__required">([^<]+)</span>', html)
    assert marker is not None
    assert "required" in marker.group(1)
    assert re.search(r"<input[^>]*\srequired", html)
    assert 'class="field__required"' not in field(SampleForm(), "note")


def test_help_text_is_tied_with_aria_describedby() -> None:
    html = field(SampleForm(), "email")
    assert 'id="id_email_helptext"' in html
    control = re.search(r"<input[^>]*>", html)
    assert control is not None
    assert 'aria-describedby="id_email_helptext"' in control.group(0)


def test_errors_are_inline_and_tied_to_the_control() -> None:
    form = SampleForm(data={"email": "not-an-email"})
    assert not form.is_valid()
    html = field(form, "email")
    control = re.search(r"<input[^>]*>", html)
    assert control is not None
    assert 'aria-invalid="true"' in control.group(0)
    described = re.search(r'aria-describedby="([^"]+)"', control.group(0))
    assert described is not None
    assert "id_email_error" in described.group(1).split()
    assert re.search(r'<ul class="errorlist"[^>]*id="id_email_error"', html)
    assert 'class="field field--invalid"' in html
