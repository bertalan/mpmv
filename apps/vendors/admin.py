"""Staff admin for vendors and their translations (Wagtail admin replaces this in CORE.8)."""

from collections.abc import Callable

from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest

from apps.core.audit import AuditContext
from apps.core.http import client_ip

from . import engine
from .models import Vendor, VendorStatus, VendorTranslation
from .services import change_status

StatusAction = Callable[[admin.ModelAdmin[Vendor], HttpRequest, QuerySet[Vendor]], None]


def audit_context(request: HttpRequest) -> AuditContext:
    user = request.user
    return AuditContext(
        actor_id=user.pk,
        actor_label=f"staff:{user.get_username()}",
        source="admin",
        ip=client_ip(request),
    )


def status_action(target: VendorStatus) -> StatusAction:
    """An admin action that moves the selected vendors to `target` through the service."""

    def action(
        modeladmin: admin.ModelAdmin[Vendor], request: HttpRequest, queryset: QuerySet[Vendor]
    ) -> None:
        ctx = audit_context(request)
        moved = 0
        for vendor in queryset:
            try:
                change_status(vendor, target, ctx=ctx)
            except engine.InvalidTransition as exc:
                modeladmin.message_user(request, f"{vendor}: {exc}", messages.ERROR)
            else:
                moved += 1
        if moved:
            modeladmin.message_user(
                request, f"{moved} vendor(s) moved to {target.label.lower()}.", messages.SUCCESS
            )

    action.__name__ = f"move_to_{target.value}"
    # Without `permissions` Django offers the action to anyone who can view the changelist.
    return admin.action(
        description=f"Move selected vendors to {target.label.lower()}", permissions=["change"]
    )(action)


class VendorTranslationInline(admin.StackedInline[VendorTranslation, Vendor]):
    model = VendorTranslation
    extra = 1
    fields = (
        "language",
        "status",
        "tagline",
        "description",
        "shipping_policy",
        "refund_policy",
        "cancellation_policy",
    )


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin[Vendor]):
    list_display = ("display_name", "legal_name", "status", "country", "contact_email")
    list_filter = ("status", "country", "sales_mode_default")
    search_fields = ("display_name", "legal_name", "slug", "vat_number", "contact_email")
    readonly_fields = ("status", "public_id", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("display_name", "slug", "status", "public_id")}),
        (
            "Legal and tax",
            {"fields": ("legal_name", "country", "vat_number", "tax_code", "registered_address")},
        ),
        ("Contact", {"fields": ("contact_email", "phone")}),
        ("Sales", {"fields": ("sales_mode_default", "enquiries_enabled")}),
        ("Migration", {"fields": ("legacy_wp_user_id",)}),
        ("Dates", {"fields": ("created_at", "updated_at")}),
    )
    inlines = (VendorTranslationInline,)
    # One action per status reachable by some transition (nothing ever moves back to applicant).
    actions = tuple(
        status_action(status)
        for status in VendorStatus
        if any(status in targets for targets in engine.TRANSITIONS.values())
    )
