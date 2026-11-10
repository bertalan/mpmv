"""Manager views (CORE.17): thin adapters, no business logic (spec 01 §1.3).

Every Manager view extends `ManagerView` and names its section; the gate
(`middleware.ManagerGateMiddleware`) and `ManagerView.dispatch` both check it. Data comes through
`scoped()`: `for_actor` (CORE.3), narrowed in the shop space to the selected vendor.
"""

from dataclasses import replace
from typing import Any, ClassVar

from django.conf import settings
from django.contrib import auth, messages
from django.contrib.auth import views as auth_views
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Model
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.http.response import HttpResponseBase
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from apps.accounts.decorators import mfa_exempt
from apps.accounts.forms import ManagerLoginForm
from apps.accounts.models import User
from apps.catalog.models import Product
from apps.catalog.services import product_kpis
from apps.core.audit import audit_context
from apps.core.models import AuditLog
from apps.vendors.models import VendorTranslation
from apps.vendors.scoping import Source, VendorScopedQuerySet
from apps.vendors.services import PROFILE_LOCKED, save_storefront_profile

from .access import ManagerContext, may_enter
from .forms import StorefrontProfileForm
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

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["products"] = product_kpis(self.scoped(Product))
        return context


class StorefrontProfileView(ManagerView):
    """The owner edits the storefront texts and policies, one language at a time (CORE.9).

    Vendor staff never get here: the section is declared for the `profile` action, which the role
    matrix gives to owners only. The service checks the same right again.
    """

    section = "shop.profile"
    template_name = "manager/shop/profile.html"

    def language(self) -> str:
        found = self.request.POST.get("language") or self.request.GET.get("language")
        language = found or settings.LANGUAGE_CODE
        if language not in dict(settings.LANGUAGES):
            raise Http404("Unknown language.")
        return language

    def stored_row(self, language: str) -> VendorTranslation | None:
        return self.scoped(VendorTranslation).filter(language=language).first()

    def stored_values(self, language: str) -> dict[str, str]:
        row = self.stored_row(language)
        return (
            {} if row is None else {n: getattr(row, n) for n in StorefrontProfileForm.base_fields}
        )

    def locked(self) -> bool:
        vendor = self.manager.vendor
        return vendor is not None and vendor.status in PROFILE_LOCKED

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        language = self.language()
        row = self.stored_row(language)
        context.setdefault("form", StorefrontProfileForm(initial=self.stored_values(language)))
        context["language"] = language
        context["locked"] = self.locked()
        context["machine_draft"] = (
            row is not None and row.status == VendorTranslation.Status.MACHINE
        )
        context["languages"] = [
            {"code": code, "name": name, "current": code == language}
            for code, name in settings.LANGUAGES
        ]
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        vendor, actor = self.manager.vendor, self.manager.user
        if vendor is None or not isinstance(actor, User):
            raise Http404("No vendor selected.")
        if self.locked():  # the reason is on the page; the service refuses it too
            return self.render_to_response(self.get_context_data(), status=403)
        language = self.language()
        form = StorefrontProfileForm(request.POST, initial=self.stored_values(language))
        if form.is_valid():
            ctx = replace(audit_context(request), source=AuditLog.Source.VENDOR)
            try:
                changes = save_storefront_profile(
                    vendor, language, form.cleaned_data, actor=actor, ctx=ctx
                )
            except ValidationError as error:
                form.add_error(None, error)
            except PermissionDenied:  # suspended between the page and the save
                return self.render_to_response(self.get_context_data(), status=403)
            else:
                messages.success(
                    request,
                    _("Storefront profile saved.")
                    if not changes.is_empty
                    else _("Nothing changed."),
                )
                return HttpResponseRedirect(
                    f"{reverse('manager:shop:profile')}?language={language}"
                )
        return self.render_to_response(self.get_context_data(form=form))


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
