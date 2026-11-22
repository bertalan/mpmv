"""The garage menu, banner and merge notice states (FIT.7)."""

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


@register.simple_tag(takes_context=True)
def garage_banner(context: template.Context) -> dict[str, Any]:
    """The state for `components/garage_banner.html` (spec 17 §17.4.4): `noun` and `label` of the
    active vehicle. Empty without an active vehicle (generic shows no banner: the header menu
    already says "generic"), without a request and on error pages. It reads the request's cached
    garage state, so a listing pays no query beyond the header menu's."""
    request = context.get("request")
    if request is None or "exception" in context:
        return {}
    state = garage_state(request)
    if not state or not state["active_label"]:
        return {}
    return {"noun": state["noun"], "label": state["active_label"]}
