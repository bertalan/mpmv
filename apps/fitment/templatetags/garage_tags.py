"""The header garage menu's state (FIT.7 1/n)."""

from typing import Any

from django import template

from apps.fitment.garage_state import garage_state

register = template.Library()


@register.simple_tag(takes_context=True)
def garage_menu(context: template.Context) -> dict[str, Any]:
    """The state for `components/garage_menu.html`. Empty without a request (the 500 page) and on
    Django's error pages (404, 400, 403), whose context carries `exception`: an error page must
    stay cheap (no query, SEC.5 resource abuse) and look the same for every path (the admin
    refusal is a normal 404, OPS.5)."""
    request = context.get("request")
    if request is None or "exception" in context:
        return {}
    return garage_state(request)
