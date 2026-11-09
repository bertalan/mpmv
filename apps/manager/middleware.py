"""The Manager gate: one check per space, for every URL under `/manager/` (CORE.17).

Spec 02 §Backoffice. Runs after the authentication and MFA middlewares, on every view under
`/manager/`, in this order:

1. a path outside both space prefixes (the Manager root included) answers 404;
2. an anonymous visitor goes to the login, with `next`;
3. a signed-in user without the space's right gets 404 (never 403: nothing to enumerate);
4. a session without a verified second factor goes to enrolment or verification (CORE.4), for
   every Manager user, also one that no `MFA_REQUIRED_POLICIES` entry covers;
5. the view must name a registered section of its space, else 404 (deny by default);
6. in the shop space the selected vendor is resolved against the user's memberships (a forged
   choice answers 404; a member of several vendors without a choice goes to the picker);
7. the section's own check, else 404.

The context lands on the request (`access.ManagerContext`, see `manager_context`); the base view
checks the section again before it answers.
"""

from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import Http404, HttpRequest, HttpResponseRedirect
from django.http.response import HttpResponseBase
from django.shortcuts import resolve_url
from django.urls import reverse

from apps.accounts.decorators import mfa_redirect
from apps.accounts.mfa_session import is_verified
from apps.accounts.services import has_second_factor
from apps.vendors.services import memberships_of

from .access import ManagerContext, may_enter
from .navigation import registry, section_key
from .spaces import MANAGER_PREFIX, Space, space_of
from .vendor_choice import selected_membership

CONTEXT_ATTR = "manager"
PICKER_URL_NAME = "manager:shop:vendor"


def manager_context(request: HttpRequest) -> ManagerContext | None:
    """The actor the gate let in, or None outside the Manager (or without the gate)."""
    ctx = getattr(request, CONTEXT_ATTR, None)
    return ctx if isinstance(ctx, ManagerContext) else None


class ManagerGateMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        return self.get_response(request)

    def process_view(
        self,
        request: HttpRequest,
        view_func: Callable[..., Any],
        view_args: tuple[Any, ...],
        view_kwargs: dict[str, Any],
    ) -> HttpResponseBase | None:
        path = request.path_info
        if not path.startswith(MANAGER_PREFIX):
            return None
        space = space_of(path)
        if space is None:
            raise Http404("No Manager page here.")
        user = request.user
        if not user.is_authenticated:
            return redirect_to_login(request.get_full_path(), resolve_url(settings.LOGIN_URL))
        if not may_enter(space, user):
            raise Http404("No Manager page here.")
        if not is_verified(request):
            return mfa_redirect(request, "mfa:verify" if has_second_factor(user) else "mfa:setup")

        key = section_key(view_func)
        section = registry.get(key) if key else None
        if section is None or section.space is not space:
            raise Http404("No Manager page here.")

        ctx = ManagerContext(user=user, space=space)
        if space is Space.SHOP:
            memberships = tuple(memberships_of(user))
            chosen = selected_membership(request, memberships)
            if chosen is None and section.needs_vendor:
                return HttpResponseRedirect(reverse(PICKER_URL_NAME))
            ctx = ManagerContext(
                user=user,
                space=space,
                vendor=chosen.vendor if chosen else None,
                role=chosen.role if chosen else None,
                memberships=memberships,
            )
        if not section.allows(ctx):
            raise Http404("No Manager page here.")
        setattr(request, CONTEXT_ATTR, ctx)
        return None
