"""The Manager gate: one check per space, for every URL under `/manager/` (CORE.17).

Spec 02 §Backoffice. Runs after the authentication and MFA middlewares, on every view under
`/manager/`, in this order:

1. a path outside both space prefixes answers 404, except the Manager root, which sends a signed-in
   user to their own space (or the 403 of step 3 when they have none);
2. an anonymous visitor goes to the login, with `next`;
3. a signed-in user without the space's right gets the explanatory 403 page (owner decision
   2026-11-10; the sections below it keep answering 404);
4. a session without a verified second factor goes to enrolment or verification (CORE.4), for
   every Manager user, also one that no `MFA_REQUIRED_POLICIES` entry covers;
5. the view must name a registered section of its space, else 404 (deny by default);
6. in the shop space the selected vendor is resolved against the user's memberships (a forged
   choice answers 404; a member of several vendors without a choice goes to the picker). A
   member whose vendors are all closed, or whose session or vendor switch names a closed vendor,
   gets the closed-vendor page (403, no vendor data) on every shop page (lane-a decision on #170);
   closed vendors are left out of the choices;
7. the section's own check, else 404.

The context lands on the request (`access.ManagerContext`, see `manager_context`); the base view
checks the section again before it answers.
"""

from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.http.response import HttpResponseBase
from django.shortcuts import render, resolve_url
from django.urls import reverse

from apps.accounts.decorators import mfa_redirect
from apps.accounts.mfa_session import is_verified
from apps.accounts.services import has_second_factor
from apps.vendors.services import memberships_of

from .access import ManagerContext, home_url, may_enter
from .navigation import registry, section_key
from .spaces import MANAGER_PREFIX, Space, space_of
from .vendor_choice import open_memberships, refused_closed, selected_membership

CONTEXT_ATTR = "manager"
PICKER_URL_NAME = "manager:shop:vendor"
PICKER_SECTION = "shop.vendor"
CLOSED_TEMPLATE = "manager/shop/vendor_closed.html"
FORBIDDEN_TEMPLATE = "manager/forbidden.html"


def vendor_closed(request: HttpRequest) -> HttpResponse:
    """403: the vendor is closed. Says how to ask for reopening (VEN.21), shows no vendor data."""
    return render(request, CLOSED_TEMPLATE, status=403)


def forbidden(request: HttpRequest) -> HttpResponse:
    """403: signed in, but without the right to this space. Explains it, lists nothing."""
    return render(request, FORBIDDEN_TEMPLATE, status=403)


def manager_context(request: HttpRequest) -> ManagerContext | None:
    """The actor the gate let in, or None outside the Manager (or without the gate)."""
    ctx = getattr(request, CONTEXT_ATTR, None)
    return ctx if isinstance(ctx, ManagerContext) else None


class ManagerGateMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponseBase]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        if request.path_info == MANAGER_PREFIX:  # no route of its own: answered before routing
            return self.root(request)
        return self.get_response(request)

    def root(self, request: HttpRequest) -> HttpResponseBase:
        """The Manager root: anonymous to the login, others to their own space or the 403."""
        user = request.user
        if not user.is_authenticated:
            return redirect_to_login(request.get_full_path(), resolve_url(settings.LOGIN_URL))
        target = home_url(user)
        return forbidden(request) if target == "/" else HttpResponseRedirect(target)

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
        user = request.user
        if space is None:
            raise Http404("No Manager page here.")
        if not user.is_authenticated:
            return redirect_to_login(request.get_full_path(), resolve_url(settings.LOGIN_URL))
        if not may_enter(space, user):
            return forbidden(request)
        if not is_verified(request):
            return mfa_redirect(request, "mfa:verify" if has_second_factor(user) else "mfa:setup")

        key = section_key(view_func)
        section = registry.get(key) if key else None
        if section is None or section.space is not space:
            raise Http404("No Manager page here.")

        ctx = ManagerContext(user=user, space=space)
        if space is Space.SHOP:
            every = memberships_of(user)
            switch_to = (
                request.POST.get("vendor", "")
                if section.key == PICKER_SECTION and request.method == "POST"
                else None
            )
            memberships = open_memberships(every)
            if refused_closed(request, every, switch_to) or not memberships:
                return vendor_closed(request)
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
