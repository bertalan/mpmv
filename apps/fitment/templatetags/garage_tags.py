"""The header garage menu's state (FIT.7 1/n)."""

from typing import Any

from django import template

from apps.fitment.garage_merge import pending_count
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


@register.simple_tag(takes_context=True)
def garage_merge_notice(context: template.Context) -> dict[str, Any]:
    """The sign-in merge notice (spec 17 §17.4.3): `count` vehicles staged from this browser's
    cookie, only for a signed-in visitor (no session read for an anonymous one) and never on an
    error page."""
    request = context.get("request")
    if request is None or "exception" in context or not hasattr(request, "session"):
        return {}
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated):
        return {}
    count = pending_count(request)
    return {"count": count} if count else {}
