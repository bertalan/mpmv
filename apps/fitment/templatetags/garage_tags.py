"""The header garage menu's state (FIT.7 1/n)."""

from typing import Any

from django import template

from apps.fitment.garage_merge import pending_count
from apps.fitment.garage_state import garage_state

register = template.Library()


@register.simple_tag(takes_context=True)
def garage_menu(context: template.Context) -> dict[str, Any]:
    """The state for `components/garage_menu.html`; empty without a request (the 500 page)."""
    request = context.get("request")
    return garage_state(request) if request is not None else {}


@register.simple_tag(takes_context=True)
def garage_merge_notice(context: template.Context) -> dict[str, Any]:
    """The sign-in merge notice (spec 17 §17.4.3): `count` vehicles staged from this browser's
    cookie, only for a signed-in visitor (no session read for an anonymous one)."""
    request = context.get("request")
    user = getattr(request, "user", None)
    if request is None or not hasattr(request, "session") or not (user and user.is_authenticated):
        return {}
    count = pending_count(request)
    return {"count": count} if count else {}
