"""Manager views (CORE.17): thin adapters, no business logic (spec 01 §1.3).

Every Manager view extends `ManagerView` and names its section; the gate
(`middleware.ManagerGateMiddleware`) and `ManagerView.dispatch` both check it. Data comes through
`scoped()`: `for_actor` (CORE.3), narrowed in the shop space to the selected vendor.
"""

import uuid
from dataclasses import replace
from typing import Any, ClassVar

from django.conf import settings
from django.contrib import auth, messages
from django.contrib.auth import views as auth_views
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Model
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.http.response import HttpResponseBase
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from apps.accounts.decorators import (
    mfa_exempt,
    mfa_redirect,
    requires_level,
    requires_level_method,
    step_up_required,
)
from apps.accounts.forms import ManagerLoginForm
from apps.accounts.mfa_session import is_recently_verified
from apps.accounts.models import User
from apps.accounts.services import has_second_factor
from apps.catalog.models import Product
from apps.catalog.services import product_kpis
from apps.core.audit import audit_context
from apps.core.models import AuditLog
from apps.core.public_forms import Protection, pre_mfa_step, public_form
from apps.core.services import flag_enabled
from apps.orders import engine as orders_engine
from apps.orders.models import VendorOrder, VendorOrderState
from apps.orders.services import fulfil_vendor_order
from apps.vendors.engine import RICH_TEXT_FIELDS, plain_to_html
from apps.vendors.models import Vendor, VendorTranslation
from apps.vendors.scoping import Source, VendorScopedQuerySet
from apps.vendors.services import (
    PROFILE_FIELDS,
    PROFILE_LOCKED,
    confirm_logo_change,
    confirmable_logo_entry_ids,
    profile_history,
    remove_vendor_logo,
    revert_logo_change,
    revert_profile_change,
    reverted_entry_ids,
    save_storefront_profile,
    set_vendor_logo,
)

from . import roles as roles_module
from . import staff_roles
from .access import ManagerContext, home_url
from .forms import ShipmentForm, StaffRoleForm, StorefrontProfileForm, VendorLogoForm
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
        if row is None:
            return {}
        # A legacy plain text is shown to the editor as the paragraphs it will be stored as.
        return {
            n: plain_to_html(getattr(row, n)) if n in RICH_TEXT_FIELDS else getattr(row, n)
            for n in StorefrontProfileForm.base_fields
        }

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
        vendor = self.manager.vendor
        current = vendor.logo if vendor is not None else None
        context.setdefault(
            "logo_form",
            VendorLogoForm(initial={"logo_alt": vendor.logo_alt if vendor else ""}),
        )
        context["logo"] = current
        context["machine_draft"] = (
            row is not None and row.status == VendorTranslation.Status.MACHINE
        )
        context["languages"] = [
            {"code": code, "name": name, "current": code == language}
            for code, name in settings.LANGUAGES
        ]
        return context

    def post_logo(self, request: HttpRequest, vendor: Vendor, actor: User) -> HttpResponse:
        """The logo form: remove, or a new image and/or its description (the service checks)."""
        form = VendorLogoForm(request.POST, request.FILES)
        if form.is_valid():
            ctx = replace(audit_context(request), source=AuditLog.Source.VENDOR)
            data = form.cleaned_data
            try:
                if data["remove"]:
                    remove_vendor_logo(vendor, actor=actor, ctx=ctx)
                else:
                    set_vendor_logo(
                        vendor, upload=data["logo"], alt=data["logo_alt"], actor=actor, ctx=ctx
                    )
            except ValidationError as error:
                form.add_error(None, error)
            except PermissionDenied:  # suspended between the page and the save
                return self.render_to_response(self.get_context_data(), status=403)
            else:
                messages.success(request, _("Logo saved."))
                return HttpResponseRedirect(reverse("manager:shop:profile"))
        return self.render_to_response(self.get_context_data(logo_form=form))

    @requires_level_method("content_edit")  # group 1: any session, CORE.26 can tighten it
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        vendor, actor = self.manager.vendor, self.manager.user
        if vendor is None or not isinstance(actor, User):
            raise Http404("No vendor selected.")
        if self.locked():  # the reason is on the page; the service refuses it too
            return self.render_to_response(self.get_context_data(), status=403)
        if request.POST.get("action") == "logo":
            return self.post_logo(request, vendor, actor)
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
                if not changes.is_empty:
                    notice = _("Storefront profile saved.")
                elif (row := self.stored_row(language)) and row.status == "machine":
                    notice = _(
                        "Nothing changed: this text is still a draft. Edit it to publish it."
                    )
                else:
                    notice = _("Nothing changed.")
                messages.success(request, notice)
                return HttpResponseRedirect(
                    f"{reverse('manager:shop:profile')}?language={language}"
                )
        return self.render_to_response(self.get_context_data(form=form))


class VendorOrderListView(ManagerView):
    """The selected vendor's sub-orders, newest first, filtered by state (ORD.6)."""

    section = "shop.orders"
    template_name = "manager/shop/orders.html"
    paginate_by = 25

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        rows = (
            self.scoped(VendorOrder).select_related("order").order_by("-order__created_at", "-id")
        )
        state = self.request.GET.get("state", "")
        if state in VendorOrderState.values:
            rows = rows.filter(state=state)
        else:
            state = ""
        page = Paginator(rows, self.paginate_by).get_page(self.request.GET.get("page"))
        query = f"state={state}&" if state else ""
        context.update(
            page_obj=page,
            orders=[
                {
                    "number": sub.order.number,
                    "placed": sub.order.created_at,
                    "state": sub.get_state_display(),
                    "total": sub.gross_total,
                    "url": reverse("manager:shop:order", args=[sub.public_id]),
                }
                for sub in page.object_list
            ],
            states=VendorOrderState.choices,
            state=state,
            previous_url=f"?{query}page={page.previous_page_number()}"
            if page.has_previous()
            else "",
            next_url=f"?{query}page={page.next_page_number()}" if page.has_next() else "",
        )
        return context


def address_lines(address: Any) -> list[str]:
    """The text values of a stored address, in stored order (an anonymised one has none)."""
    if not isinstance(address, dict):
        return []
    return [str(v) for v in address.values() if isinstance(v, str) and v]


class VendorOrderDetailView(ManagerView):
    """One sub-order with what the vendor needs to ship it, and the actions it may take now.

    Privacy: fields are picked one by one; the buyer's e-mail and billing data never reach the
    template (VendorOrder docstring).
    """

    section = "shop.orders"
    template_name = "manager/shop/order.html"

    def sub_order(self) -> VendorOrder:
        return get_object_or_404(
            self.scoped(VendorOrder).select_related("order"), public_id=self.kwargs["public_id"]
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        sub = self.sub_order()
        order = sub.order
        context.update(
            sub=sub,
            number=order.number,
            placed=order.created_at,
            customer_name=order.customer_name,
            # Minimisation (D-30): the address is for shipping, so not after delivery nor once
            # the order is restricted or anonymised.
            show_address=sub.state in {"pending", "accepted", "shipped"}
            and order.privacy_status == "active",
            address=address_lines(order.shipping_address),
            country=order.shipping_country,
            lines=[
                {
                    "title": line.title,
                    "variant": line.variant_label,
                    "sku": line.sku,
                    "quantity": line.quantity,
                }
                for line in sub.lines.all()
            ],
            can_accept=sub.state == "pending",
            can_ship=sub.state == "accepted",
            can_deliver=sub.state == "shipped",
            accept_url=reverse("manager:shop:order_action", args=[sub.public_id, "accept"]),
            ship_url=reverse("manager:shop:order_action", args=[sub.public_id, "ship"]),
            deliver_url=reverse("manager:shop:order_action", args=[sub.public_id, "deliver"]),
        )
        context.setdefault("form", ShipmentForm(initial={"carrier": sub.carrier}))
        return context


class VendorOrderActionView(VendorOrderDetailView):
    """POST accept / ship / deliver on one of the vendor's own sub-orders (ORD.6)."""

    http_method_names = ["post", "options"]

    def refused(self, sub: VendorOrder) -> HttpResponse:
        context = self.get_context_data(
            action_error=_("This step is not possible in the order's current state.")
        )
        return self.render_to_response(context, status=409)

    @requires_level_method("orders_in_progress")  # group 1: any session, CORE.26 can tighten it
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        action = self.kwargs["action"]
        if action not in orders_engine.FULFILMENT_ACTIONS:
            raise Http404("No such action.")
        sub = self.sub_order()  # another vendor's id is 404 here, before anything runs
        actor = self.manager.user
        if not isinstance(actor, User):
            raise Http404("No such order.")
        tracking, carrier = "", ""
        if action == "ship":
            if sub.state != "accepted":  # the state first: a form for it is not even shown
                return self.refused(sub)
            form = ShipmentForm(request.POST)
            if not form.is_valid():
                return self.render_to_response(self.get_context_data(form=form), status=400)
            tracking, carrier = form.cleaned_data["tracking_number"], form.cleaned_data["carrier"]
        ctx = replace(audit_context(request), source=AuditLog.Source.VENDOR)
        try:
            fulfil_vendor_order(
                sub, action, actor=actor, ctx=ctx, tracking_number=tracking, carrier=carrier
            )
        except (orders_engine.IllegalTransition, ValueError):
            return self.refused(sub)
        messages.success(request, _("The order was updated."))
        return HttpResponseRedirect(reverse("manager:shop:order", args=[sub.public_id]))


class PlatformDashboardView(ManagerView):
    section = "platform.dashboard"
    template_name = "manager/platform/dashboard.html"


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError):
        return False
    return True


REVERT_ACTIONS = ("vendor.profile_reverted", "vendor.logo_reverted")


class ProfileChangesView(ManagerView):
    """Staff review the owners' storefront profile changes and see the reverts (CORE.9 3/3).

    Platform space only (vendor members never get here, so a vendor sees no history), and only for
    holders of the revert permission. Optional `?vendor=<public id>` narrows the list.
    """

    section = "platform.profile_changes"
    template_name = "manager/platform/profile_changes.html"

    def selected_vendor(self) -> Vendor | None:
        found = self.request.GET.get("vendor", "")
        if not found:
            return None
        try:
            return Vendor.objects.filter(public_id=found).first()
        except (ValidationError, ValueError):
            return None

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        vendor = self.selected_vendor()
        user = self.manager.user
        entries = profile_history(user, vendor=vendor) if isinstance(user, User) else []
        reverted = reverted_entry_ids(entries)
        confirmable = confirmable_logo_entry_ids(entries)
        keys = {e.vendor_key for e in entries if _is_uuid(e.vendor_key)}
        names = {
            str(k): v
            for k, v in (
                Vendor.objects.filter(public_id__in=keys).values_list("public_id", "display_name")
            )
        }
        rows = []
        for entry in entries:
            before = entry.before if isinstance(entry.before, dict) else {}
            after = entry.after if isinstance(entry.after, dict) else {}
            rows.append(
                {
                    "entry": entry,
                    "vendor": names.get(entry.vendor_key, entry.vendor_key),
                    "language": after.get("language") or _("all languages"),
                    "is_revert": entry.action in REVERT_ACTIONS,
                    "reason": after.get("reason", ""),
                    "reverted": str(entry.public_id) in reverted,
                    "confirmable": str(entry.public_id) in confirmable,
                    "diff": [
                        {"field": name, "before": before.get(name, ""), "after": after[name]}
                        for name in (*PROFILE_FIELDS, "logo", "logo_alt")
                        if name in after
                    ],
                }
            )
        context["rows"] = rows
        context["filter_vendor"] = vendor
        return context


class ProfileRevertView(ManagerView):
    """Revert one change, with a required reason (POST only, CSRF-protected)."""

    section = "platform.profile_changes"
    http_method_names = ["post"]

    @requires_level_method("profile_revert")  # group 2 (CORE.25)
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        actor = self.manager.user
        if not isinstance(actor, User):
            raise Http404("No Manager page here.")
        if not is_recently_verified(request):  # step-up, as the other sensitive staff actions
            target = "mfa:verify" if has_second_factor(actor) else "mfa:setup"
            return mfa_redirect(
                request, target, next_url=reverse("manager:platform:profile_changes")
            )
        ctx = replace(audit_context(request), source=AuditLog.Source.ADMIN)
        is_logo = AuditLog.objects.filter(
            public_id=kwargs["entry"], action="vendor.logo_changed"
        ).exists()
        revert = revert_logo_change if is_logo else revert_profile_change
        try:
            revert(kwargs["entry"], request.POST.get("reason", ""), actor=actor, ctx=ctx)
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
        except PermissionDenied as error:
            raise Http404("No Manager page here.") from error
        else:
            messages.success(request, _("The change was reverted and the owners were told."))
        return HttpResponseRedirect(reverse("manager:platform:profile_changes"))


class ProfileConfirmView(ManagerView):
    """Confirm a logo change: the previous file kept for a revert is deleted now (owner decision
    Q1). POST only, CSRF-protected, same level and step-up as the revert."""

    section = "platform.profile_changes"
    http_method_names = ["post"]

    @requires_level_method("profile_revert")  # group 2 (CORE.25)
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        actor = self.manager.user
        if not isinstance(actor, User):
            raise Http404("No Manager page here.")
        if not is_recently_verified(request):
            target = "mfa:verify" if has_second_factor(actor) else "mfa:setup"
            return mfa_redirect(
                request, target, next_url=reverse("manager:platform:profile_changes")
            )
        ctx = replace(audit_context(request), source=AuditLog.Source.ADMIN)
        try:
            confirm_logo_change(kwargs["entry"], actor=actor, ctx=ctx)
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
        except PermissionDenied as error:
            raise Http404("No Manager page here.") from error
        else:
            messages.success(request, _("The change is confirmed and the old logo is deleted."))
        return HttpResponseRedirect(reverse("manager:platform:profile_changes"))


class VendorPickerView(ManagerView):
    """Choose the vendor to work on, among the user's own vendors (GET lists, POST selects)."""

    section = "shop.vendor"
    template_name = "manager/shop/vendor_picker.html"

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        choose(request, self.manager.memberships, request.POST.get("vendor", ""))
        return HttpResponseRedirect(reverse("manager:shop:dashboard"))


# --- Sign-in and sign-out: outside /manager/, so the sweep keeps every Manager URL gated


@public_form(
    protection=Protection.CAPTCHA,
    purpose="Sign in to the Manager (every Manager user)",
    task="CORE.17",
    rate_limit="per account and IP lockout (CORE.5)",
    templates=("manager/login.html",),
)
class LoginView(auth_views.LoginView):
    """One login for every Manager user (spec 02 §Backoffice).

    `authenticate()` runs the CORE.5 backend (lockout, captcha); the second factor follows on the
    MFA pages, where the gate sends a signed-in user without a verified session.
    """

    template_name = "manager/login.html"
    authentication_form = ManagerLoginForm

    def get_default_redirect_url(self) -> str:
        return home_url(self.request.user)


@pre_mfa_step(purpose="Sign out, also before the second factor is verified", task="CORE.17")
@mfa_exempt
@require_POST
def logout(request: HttpRequest) -> HttpResponse:
    """Sign out (POST only, CSRF-protected), also before the second factor is verified."""
    auth.logout(request)
    return HttpResponseRedirect(reverse("login"))


login = LoginView.as_view()


class StaffRolesView(ManagerView):
    """ "Staff and roles" (CORE.18 2/2): the platform owner lists, grants and revokes roles.

    A thin adapter over `staff_roles`. Granting or revoking is a sensitive action: the session
    must be in operational mode (`role_grant`), and the owner role also needs a fresh passkey
    (`owner_role_grant`), exactly as in the Django admin. The service refuses what the rules of
    spec 02 forbid, and the page shows the refusal.
    """

    section = "platform.staff_roles"
    template_name = "manager/platform/staff_roles.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context.setdefault("form", StaffRoleForm(initial={"action": "grant"}))
        context["members"] = [
            {
                "user": user,
                "roles": [roles_module.role(k) for k in roles_module.ROLE_KEYS if k in held],
            }
            for user, held in staff_roles.staff_members()
        ]
        context["one_person_team"] = flag_enabled(staff_roles.ONE_PERSON_TEAM)
        return context

    @requires_level_method("role_grant")  # group 2 (CORE.25); the owner role adds the next gate
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        if request.POST.get("role", "") == roles_module.OWNER:
            return requires_level("owner_role_grant")(step_up_required()(self._change))(request)
        return step_up_required()(self._change)(request)

    def _change(self, request: HttpRequest, /) -> HttpResponse:
        form = StaffRoleForm(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        data = form.cleaned_data
        target = User.objects.filter(email__iexact=data["email"]).first()
        if target is None:
            form.add_error("email", _("There is no account with that email."))
            return self.render_to_response(self.get_context_data(form=form))
        change = staff_roles.grant_role if data["action"] == "grant" else staff_roles.revoke_role
        try:
            change(
                target,
                data["role"],
                actor=request.user,
                ctx=audit_context(request),
                reason=data["reason"],
            )
        except staff_roles.RoleAssignmentError as error:
            form.add_error(None, str(error))
            return self.render_to_response(self.get_context_data(form=form))
        except PermissionDenied:
            raise Http404("No such action.") from None
        messages.success(request, _("The role change is recorded."))
        return HttpResponseRedirect(reverse("manager:platform:staff_roles"))
