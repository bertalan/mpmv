"""Template filters for the theme components (THM.4, spec 14 §14.9).

- `{{ url|safe_href }}`: the URL when it is http(s) or relative ("/", "#", "?"), else "", so a
  component wraps its link in `{% if href %}` and renders no link for anything else.
- `{{ heading_level|heading_level:3 }}`: the heading level clamped to 2-6, the argument being
  the default for a missing or unreadable value.
- `{{ field|bs_widget }}`: a bound form field rendered with the Bootstrap form class of its
  widget (and `is-invalid` when it has errors), for themes on a Bootstrap foundation (THM.16).
"""

import json

from django import forms, template
from django.forms.boundfield import BoundField
from django.utils.safestring import SafeString

from .. import engine
from ..appearance import active_payment_appearance

register = template.Library()


@register.filter
def safe_href(value: object) -> str:
    return engine.safe_href(value)


@register.filter
def heading_level(value: object, default: int = 3) -> int:
    return engine.heading_level(value, default)


@register.simple_tag
def payment_appearance_json() -> str:
    """The Stripe Payment Element appearance of the active theme as JSON, for a data attribute."""
    return json.dumps(active_payment_appearance(), sort_keys=True)


_FORM_CLASS = {
    forms.Select: "form-select",
    forms.SelectMultiple: "form-select",
    forms.CheckboxInput: "form-check-input",
    forms.RadioSelect: "form-check-input",
    forms.CheckboxSelectMultiple: "form-check-input",
}
_UNSTYLED = (forms.HiddenInput, forms.FileInput, forms.ClearableFileInput)


@register.filter
def bs_widget(field: BoundField) -> SafeString:
    """The field's widget with the Bootstrap control class added to the classes it already has."""
    widget = field.field.widget
    if isinstance(widget, _UNSTYLED):
        return SafeString(str(field))
    css = next((c for kind, c in _FORM_CLASS.items() if isinstance(widget, kind)), "form-control")
    classes = [*widget.attrs.get("class", "").split(), css]
    if field.errors and css != "form-check-input":
        classes.append("is-invalid")
    return SafeString(field.as_widget(attrs={"class": " ".join(dict.fromkeys(classes))}))
