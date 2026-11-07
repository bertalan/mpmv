"""Staff admin for vendors, their translations and the legal field requirements (Wagtail
admin replaces this in CORE.8)."""

from collections.abc import Callable
from typing import Any

from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest

from apps.core.audit import AuditContext
from apps.core.http import client_ip

from . import engine
from .models import (
    LegalFieldRequirement,
    Vendor,
    VendorStatus,
    VendorTranslation,
    legal_field_label,
)
from .services import (
    change_status,
    delete_requirement,
    legal_profile_completeness,
    save_requirement,
    save_vendor,
)

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


def _labels(names: tuple[str, ...]) -> str:
    return ", ".join(legal_field_label(name) for name in names)


def _address(prefix: str) -> tuple[str, ...]:
    return tuple(
        f"{prefix}_{name}"
        for name in ("street", "street_number", "postal_code", "city", "province", "country")
    )


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin[Vendor]):
    list_display = ("display_name", "legal_name", "status", "country", "contact_email")
    list_filter = ("status", "country", "sales_mode_default", "legal_form")
    search_fields = ("display_name", "legal_name", "slug", "vat_number", "contact_email")
    readonly_fields = (
        "status",
        "public_id",
        "missing_for_payout",
        "vies_status",
        "vies_checked_at",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (None, {"fields": ("display_name", "slug", "status", "public_id")}),
        ("Payout readiness", {"fields": ("missing_for_payout",)}),
        (
            "Legal identity",
            {"fields": ("legal_name", "country", "legal_form", "first_name", "last_name")},
        ),
        ("Registered office", {"fields": _address("registered")}),
        (
            "Tax identity",
            {
                "fields": (
                    "vat_number",
                    "vies_status",
                    "vies_checked_at",
                    "tax_code",
                    "fiscal_regime",
                    "ateco_code",
                    "eori",
                )
            },
        ),
        (
            "Company register (REA)",
            {
                "fields": (
                    "rea_office",
                    "rea_number",
                    "share_capital",
                    "sole_shareholder",
                    "liquidation_status",
                )
            },
        ),
        ("E-invoicing", {"fields": ("sdi_recipient_code", "pec")}),
        (
            "Fiscal representative (non-EU vendors)",
            {
                "classes": ("collapse",),
                "fields": ("fiscal_representative_name", "fiscal_representative_vat_number"),
            },
        ),
        (
            "Permanent establishment",
            {"classes": ("collapse",), "fields": _address("establishment")},
        ),
        ("Invoicing bank details", {"fields": ("iban", "bic", "bank_name")}),
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

    @admin.display(description="Missing for payout")
    def missing_for_payout(self, obj: Vendor | None) -> str:
        """Required legal fields (`LegalFieldRequirement`) a payout request would still need."""
        if obj is None or obj.pk is None:
            return "-"
        result = legal_profile_completeness(obj)
        if result.complete:
            return "Nothing: the legal profile is complete for payouts."
        parts = []
        if result.missing:
            parts.append("Missing: " + _labels(result.missing))
        if result.invalid:
            parts.append("Invalid: " + _labels(result.invalid))
        return ". ".join(parts) + "."

    def save_model(self, request: HttpRequest, obj: Vendor, form: Any, change: bool) -> None:
        save_vendor(obj, ctx=audit_context(request))


@admin.register(LegalFieldRequirement)
class LegalFieldRequirementAdmin(admin.ModelAdmin[LegalFieldRequirement]):
    """Which legal fields vendors need, per country and legal form. Every change is audited."""

    list_display = ("field_name", "country", "legal_form", "purpose", "active", "note")
    list_filter = ("purpose", "active", "country", "legal_form", "field_name")
    search_fields = ("field_name", "country", "note")
    fields = ("country", "legal_form", "field_name", "purpose", "active", "note")

    def save_model(
        self, request: HttpRequest, obj: LegalFieldRequirement, form: Any, change: bool
    ) -> None:
        save_requirement(obj, ctx=audit_context(request))

    def delete_model(self, request: HttpRequest, obj: LegalFieldRequirement) -> None:
        delete_requirement(obj, ctx=audit_context(request))

    def delete_queryset(
        self, request: HttpRequest, queryset: QuerySet[LegalFieldRequirement]
    ) -> None:
        ctx = audit_context(request)
        for requirement in queryset:
            delete_requirement(requirement, ctx=ctx)
