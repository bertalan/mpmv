"""Template filters for the theme components (THM.4, spec 14 §14.9).

- `{{ url|safe_href }}`: the URL when it is http(s) or relative ("/", "#", "?"), else "", so a
  component wraps its link in `{% if href %}` and renders no link for anything else.
- `{{ heading_level|heading_level:3 }}`: the heading level clamped to 2-6, the argument being
  the default for a missing or unreadable value.
"""

from django import template

from .. import engine

register = template.Library()


@register.filter
def safe_href(value: object) -> str:
    return engine.safe_href(value)


@register.filter
def heading_level(value: object, default: int = 3) -> int:
    return engine.heading_level(value, default)
