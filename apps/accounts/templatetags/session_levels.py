"""The session level banner (D-32, CORE.25).

{% load session_levels %}
{% session_level_banner %}   in the header of staff pages (needs `request` in the context)

Operational mode: a banner with the minutes left and an "End now" button. Secure mode: a short
note for users who may switch. Nothing for anonymous visitors and customers.
"""

from django import template
from django.http import HttpRequest
from django.middleware.csrf import get_token
from django.urls import reverse
from django.utils.http import urlencode

from apps.accounts import levels

register = template.Library()


@register.inclusion_tag("accounts/levels/_banner.html", takes_context=True)
def session_level_banner(context: template.Context) -> dict[str, object]:
    request: HttpRequest | None = context.get("request")
    user = getattr(request, "user", None)
    if request is None or user is None or not user.is_authenticated:
        return {"mode": None}
    left = levels.seconds_left(request)
    if left is not None:
        return {
            "mode": levels.OPERATIONAL,
            "minutes_left": max(1, -(-left // 60)),
            "request": request,
        }
    if user.is_staff or levels.may_elevate(user):
        security = (
            reverse("mfa:setup")
            + "?"
            + urlencode({"next": request.get_full_path(), "from": "security"})
        )
        return {
            "mode": levels.RESTRICTED,
            "request": request,
            "may_elevate": levels.may_elevate(user),
            "can_elevate": user.has_perm(f"core.{levels.ELEVATE_CODENAME}"),
            "security_url": security,
            "csrf_token": get_token(request),
            "csp_nonce": context.get("csp_nonce", ""),
        }
    return {"mode": None}
