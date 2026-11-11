"""Manager pages for the vendor migration choices (MIG.16, owner decision D-16).

Thin adapters over `apps.ingest.services`. In the shop space the owner of the selected vendor
records what to migrate; in the platform space staff read every vendor's choice and, with the
edit right on `platform.vendors` (CORE.18), enter it on a vendor's behalf (audited, source
"staff"). A request that names another vendor than the selected one answers 404 and writes an
audit entry (CORE.3).
"""

import uuid
from dataclasses import replace
from typing import Any

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.core.audit import audit_context
from apps.core.models import AuditLog
from apps.core.services import is_platform_owner
from apps.ingest import engine, services
from apps.ingest.models import MigrationChoice
from apps.vendors.models import Vendor

from . import roles
from .forms import MigrationChoiceForm
from .role_groups import user_roles
from .views import ManagerView

SHOP_SECTION = "shop.migration"
PLATFORM_SECTION = "platform.migration"


def platform_role_keys(user: Any) -> frozenset[str]:
    keys = set(user_roles(user))
    if is_platform_owner(user):
        keys.add(roles.OWNER)
    return frozenset(keys)


def can_read_choices(ctx: Any) -> bool:
    return roles.allows(PLATFORM_SECTION, platform_role_keys(ctx.user))


def can_enter_choices(user: Any) -> bool:
    return roles.allows(PLATFORM_SECTION, platform_role_keys(user), write=True)


class _ChoiceFormView(ManagerView):
    """The form, its context and the save, shared by the shop and the platform pages."""

    template_name = "manager/migration.html"
    source = engine.SOURCE_VENDOR
    audit_source = AuditLog.Source.VENDOR

    def vendor_of_page(self) -> Vendor:
        raise NotImplementedError

    def may_write(self) -> bool:
        return True

    def post_url(self) -> str:
        raise NotImplementedError

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        vendor = self.vendor_of_page()
        now = timezone.now()
        run = services.active_run(now)
        current = services.current_choice(vendor, run) if run else None
        values = current.values if current else engine.DEFAULT_CHOICE
        deadline = run.choice_deadline if run else None
        closed = deadline is not None and now >= deadline
        context.setdefault(
            "form", MigrationChoiceForm(initial=MigrationChoiceForm.initial_for(values))
        )
        context.update(
            vendor=vendor,
            run=run,
            current=current,
            deadline=deadline,
            closed=closed,
            can_edit=self.may_write()
            and run is not None
            and (not closed or self.source != engine.SOURCE_VENDOR),
            history=services.choice_history(vendor, run) if run else [],
            defaults=engine.DEFAULT_CHOICE,
            post_url=self.post_url(),
            restricted_years=10,
        )
        return context

    def audit_ctx(self, request: HttpRequest) -> Any:
        return replace(audit_context(request), source=self.audit_source)

    def save(self, request: HttpRequest) -> HttpResponse:
        vendor, actor = self.vendor_of_page(), self.manager.user
        now = timezone.now()
        run = services.active_run(now)
        if run is None or not isinstance(actor, User) or not self.may_write():
            raise Http404("No migration is scheduled.")
        form = MigrationChoiceForm(request.POST)
        if form.is_valid():
            try:
                changes = services.record_choice(
                    vendor,
                    run,
                    form.to_values(),
                    source=self.source,
                    actor=actor,
                    ctx=self.audit_ctx(request),
                    now=now,
                )
            except services.ChoiceClosed:
                return self.render_to_response(
                    self.get_context_data(form=form, closed=True), status=403
                )
            except ValidationError as error:
                form.add_error(None, error)
            else:
                messages.success(
                    request,
                    _("Migration choice saved.") if not changes.is_empty else _("Nothing changed."),
                )
                return HttpResponseRedirect(self.post_url())
        return self.render_to_response(self.get_context_data(form=form), status=400)


class ShopMigrationChoiceView(_ChoiceFormView):
    """The owner's form for the selected vendor (`shop.migration`)."""

    section = SHOP_SECTION

    def vendor_of_page(self) -> Vendor:
        vendor = self.manager.vendor
        if vendor is None:  # the section needs a selected vendor: not reachable
            raise Http404("No vendor selected.")
        return vendor

    def post_url(self) -> str:
        return reverse("manager:shop:migration")

    def guard(self, request: HttpRequest) -> None:
        """Another vendor's id, in the query or the form, is a 404 and an audit entry."""
        requested = (request.POST.get("vendor") or request.GET.get("vendor") or "").strip()
        own = self.vendor_of_page()
        if requested and requested != str(own.public_id):
            services.record_denied_access(
                ctx=self.audit_ctx(request), own_vendor=own, requested=requested
            )
            raise Http404("No vendor matches the given query.")

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        self.guard(request)
        return super().get(request, *args, **kwargs)

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        self.guard(request)
        return self.save(request)


class PlatformMigrationListView(ManagerView):
    """Staff: every open vendor and where its choice stands for the next cutover."""

    section = PLATFORM_SECTION
    template_name = "manager/platform/migration_choices.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        now = timezone.now()
        run = services.active_run(now)
        rows = []
        if run is not None:
            latest: dict[int, MigrationChoice] = {}
            for stored in MigrationChoice.objects.filter(run=run).order_by("version"):
                latest[stored.vendor_id] = stored
            for vendor in Vendor.objects.exclude(status=Vendor.Status.CLOSED).order_by(
                "display_name", "pk"
            ):
                choice = latest.get(vendor.pk)
                rows.append(
                    {
                        "vendor": vendor,
                        "choice": choice,
                        "url": reverse(
                            "manager:platform:migration_choice", args=[vendor.public_id]
                        ),
                    }
                )
        context.update(run=run, rows=rows, deadline=run.choice_deadline if run else None)
        return context


class PlatformMigrationChoiceView(_ChoiceFormView):
    """Staff: one vendor's choice; entering it on the vendor's behalf needs the edit right."""

    section = PLATFORM_SECTION
    source = engine.SOURCE_STAFF
    audit_source = AuditLog.Source.ADMIN

    def vendor_of_page(self) -> Vendor:
        try:
            public_id = uuid.UUID(str(self.kwargs["public_id"]))
            return Vendor.objects.get(public_id=public_id)
        except (Vendor.DoesNotExist, ValueError):
            raise Http404("No such vendor.") from None

    def may_write(self) -> bool:
        return can_enter_choices(self.manager.user)

    def post_url(self) -> str:
        return reverse("manager:platform:migration_choice", args=[self.vendor_of_page().public_id])

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        return self.save(request)


__all__ = [
    "PlatformMigrationChoiceView",
    "PlatformMigrationListView",
    "ShopMigrationChoiceView",
    "can_read_choices",
]
