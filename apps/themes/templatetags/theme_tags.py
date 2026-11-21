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
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString

from .. import engine
from ..appearance import active_payment_appearance
from ..services import palette_href

register = template.Library()


@register.filter
def safe_href(value: object) -> str:
    return engine.safe_href(value)


@register.filter
def cart_item_count(packages: object) -> int:
    """The number of items in the cart's packages (the sum of the line quantities), 0 if none."""
    if not isinstance(packages, list | tuple):
        return 0
    return sum(
        int(getattr(line, "quantity", 0) or 0)
        for package in packages
        for line in getattr(package, "lines", [])
    )


@register.filter
def heading_level(value: object, default: int = 3) -> int:
    return engine.heading_level(value, default)


@register.simple_tag
def payment_appearance_json() -> str:
    """The Stripe Payment Element appearance of the active theme as JSON, for a data attribute."""
    return json.dumps(active_payment_appearance(), sort_keys=True)


_CHOICE_GROUPS = (forms.RadioSelect, forms.CheckboxSelectMultiple)
_FORM_CLASS = {
    forms.Select: "form-select",
    forms.SelectMultiple: "form-select",
    forms.CheckboxInput: "form-check-input",
}


def _classes(existing: str, *added: str) -> str:
    return " ".join(dict.fromkeys([*existing.split(), *added]))


def _choice_group(field: BoundField) -> SafeString:
    """A radio or checkbox group: the wrapper `<div id>` keeps no class; each option is a
    Bootstrap form check whose input carries `form-check-input`."""
    widget = field.field.widget
    extra = ["is-invalid"] if field.errors else []
    options = []
    for sub in field.subwidgets:
        data = dict(sub.data)
        attrs = dict(data["attrs"])
        attrs["class"] = _classes(attrs.get("class", ""), "form-check-input", *extra)
        control = widget._render(
            sub.template_name, {"widget": {**data, "attrs": attrs, "wrap_label": False}}, None
        )
        options.append(
            format_html(
                '<div class="form-check">{}'
                '<label class="form-check-label" for="{}">{}</label></div>',
                control,
                sub.id_for_label,
                sub.choice_label,
            )
        )
    return format_html(
        '<div id="{}">{}</div>', field.auto_id, format_html_join("", "{}", ((o,) for o in options))
    )


@register.filter
def bs_widget(field: BoundField) -> SafeString:
    """The field's widget with the Bootstrap control class added to the classes it already has.

    A radio or checkbox group gets the class on each option input, never on its wrapper;
    hidden inputs are left alone; file inputs are `form-control`.
    """
    widget = field.field.widget
    if isinstance(widget, forms.HiddenInput):
        return SafeString(str(field))
    if isinstance(widget, _CHOICE_GROUPS):
        return _choice_group(field)
    css = next((c for kind, c in _FORM_CLASS.items() if isinstance(widget, kind)), "form-control")
    classes = _classes(
        widget.attrs.get("class", ""), css, *(["is-invalid"] if field.errors else [])
    )
    return SafeString(field.as_widget(attrs={"class": classes}))


@register.filter
def in_groups(value: object, size: object) -> list[list[object]]:
    """`value` as consecutive lists of `size` items (the last may be shorter), for a carousel.

    A missing, unreadable or non-positive size means one item per group, so a template with a
    bad `per_view` still renders every item.
    """
    items = list(value) if isinstance(value, list | tuple) else []
    try:
        step = int(str(size))
    except ValueError:
        step = 1
    step = step if step > 0 else 1
    return [items[start : start + step] for start in range(0, len(items), step)]


@register.simple_tag(takes_context=True)
def palette_css_href(context: template.Context) -> str:
    """The URL of the site palette stylesheet to link after `theme.css`, or "" (spec 14 §14.12).

    Error pages (`minimal_chrome`) never touch the database, so they keep the theme's colours.
    """
    request = context.get("request")
    if request is None or context.get("minimal_chrome"):
        return ""
    return palette_href(request)
