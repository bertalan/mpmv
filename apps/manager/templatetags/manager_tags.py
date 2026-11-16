"""Template tag that lets vendor members and platform staff reach the Manager (CORE.30)."""

from typing import Any

from django import template
from django.utils.translation import gettext as _

from apps.manager.access import home_url, may_enter
from apps.manager.spaces import Space

register = template.Library()


@register.simple_tag(takes_context=True)
def manager_entry(context: template.Context) -> dict[str, str] | None:
    """The "My shop" or "Manager" entry for this user, or None for visitors and customers.

    Both link to the Manager home of the user's own space (`home_url`).
    """
    request: Any = context.get("request")
    user = getattr(request, "user", None) or context.get("user")
    if user is None:
        return None
    if may_enter(Space.PLATFORM, user):
        label = _("Manager")
    elif may_enter(Space.SHOP, user):
        label = _("My shop")
    else:
        return None
    return {"label": label, "url": home_url(user)}
