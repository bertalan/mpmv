"""Manager views (CORE.17): thin adapters, no business logic (spec 01 §1.3).

Every Manager view extends `ManagerView` and names its section; the gate
(`middleware.ManagerGateMiddleware`) and `ManagerView.dispatch` both check it. Data comes through
`scoped()`: `for_actor` (CORE.3), narrowed in the shop space to the selected vendor.
"""

from typing import Any, ClassVar

from django.contrib import auth
from django.contrib.auth import views as auth_views
from django.db.models import Model
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.http.response import HttpResponseBase
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from apps.accounts.decorators import mfa_exempt
from apps.accounts.forms import ManagerLoginForm
from apps.vendors.scoping import Source, VendorScopedQuerySet

from .access import ManagerContext, may_enter
from .middleware import manager_context
from .navigation import Section, registry
from .spaces import Space
from .vendor_choice import choose


class ManagerView(TemplateView):
    """Base of every Manager page: the section check, the shell context and tenant scoping."""

    section: ClassVar[str]
    title: ClassVar[str] = ""

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        ctx = manager_context(request)
        section = registry.get(getattr(self, "section", ""))
        if ctx is None or section is None or not section.allows(ctx):
            raise Http404("No Manager page here.")  # also when the gate is not installed
        self.manager = ctx
        self.manager_section = section
        return super().dispatch(request, *args, **kwargs)

    def scoped[M: Model](self, source: Source[M]) -> VendorScopedQuerySet[M]:
        """`source` as the actor may see it: the selected vendor's rows in the shop space."""
        queryset = source._default_manager.all() if isinstance(source, type) else source.all()
        if not isinstance(queryset, VendorScopedQuerySet):
            raise TypeError(f"{queryset.model._meta.label} is not vendor-scoped")
        rows = queryset.for_actor(self.manager.user)
        if self.manager.space is Space.SHOP:
            if self.manager.vendor is None:
                return rows.none()
            rows = rows.for_vendor(self.manager.vendor)
        return rows

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context.update(shell_context(self.manager, self.manager_section))
        context.setdefault("title", self.title or self.manager_section.label)
        return context


def shell_context(ctx: ManagerContext, current: Section) -> dict[str, Any]:
    """The layout's context: the menu, the current section and the vendor switcher."""
    menu = [
        {"label": s.label, "url": reverse(s.url_name), "current": s.key == current.key}
        for s in registry.menu(ctx)
    ]
    return {
        "manager": ctx,
        "manager_menu": menu,
        "manager_home": reverse(f"manager:{ctx.space}:dashboard"),
        "vendor_switcher": [m.vendor for m in ctx.memberships] if len(ctx.memberships) > 1 else [],
    }


# --- Built-in sections (registered in sections.py)


class ShopDashboardView(ManagerView):
    section = "shop.dashboard"
    template_name = "manager/shop/dashboard.html"


class PlatformDashboardView(ManagerView):
    section = "platform.dashboard"
    template_name = "manager/platform/dashboard.html"


class VendorPickerView(ManagerView):
    """Choose the vendor to work on, among the user's own vendors (GET lists, POST selects)."""

    section = "shop.vendor"
    template_name = "manager/shop/vendor_picker.html"

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        choose(request, self.manager.memberships, request.POST.get("vendor", ""))
        return HttpResponseRedirect(reverse("manager:shop:dashboard"))


# --- Sign-in and sign-out: outside /manager/, so the sweep keeps every Manager URL gated


def home_url(user: Any) -> str:
    """Where a user lands after signing in without `next`: their Manager space, else the site."""
    for space in (Space.PLATFORM, Space.SHOP):
        if may_enter(space, user):
            return reverse(f"manager:{space}:dashboard")
    return "/"


class LoginView(auth_views.LoginView):
    """One login for every Manager user (spec 02 §Backoffice).

    `authenticate()` runs the CORE.5 backend (lockout, captcha); the second factor follows on the
    MFA pages, where the gate sends a signed-in user without a verified session.
    """

    template_name = "manager/login.html"
    authentication_form = ManagerLoginForm

    def get_default_redirect_url(self) -> str:
        return home_url(self.request.user)


@mfa_exempt
@require_POST
def logout(request: HttpRequest) -> HttpResponse:
    """Sign out (POST only, CSRF-protected), also before the second factor is verified."""
    auth.logout(request)
    return HttpResponseRedirect(reverse("login"))


login = LoginView.as_view()
