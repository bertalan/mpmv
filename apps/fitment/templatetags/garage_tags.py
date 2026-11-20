"""The header garage menu's state (FIT.7 1/n)."""

from typing import Any

from django import template

from apps.fitment.garage_state import garage_state

register = template.Library()


@register.simple_tag(takes_context=True)
def garage_menu(context: template.Context) -> dict[str, Any]:
    """The state for `components/garage_menu.html`; empty without a request (the 500 page)."""
    request = context.get("request")
    return garage_state(request) if request is not None else {}
