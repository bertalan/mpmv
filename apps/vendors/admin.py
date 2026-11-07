"""Staff admin for vendors, their translations and the legal field requirements
(Wagtail admin replaces this in CORE.8).

Vendor writes go through the audited services (SEC.4); the change page links to the vendor's
audit trail.
"""

from collections.abc import Callable
from typing import Any

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString

from apps.core.admin import step_up
from apps.core.audit import audit_context

from . import engine
from .models import (
    ContractAcceptance,
    LegalFieldRequirement,
    Vendor,
    VendorContract,
    VendorContractTranslation,
    VendorFeatureFlag,
    VendorStatus,
    VendorTranslation,
    legal_field_label,
)
from .services import (
    change_status,
    check_contract_start,
    current_contract,
    delete_requirement,
    delete_vendor,
    delete_vendor_flag,
    legal_profile_completeness,
    onboarding_checklist,
    save_contract,
    save_requirement,
    save_vendor,
    save_vendor_flag,
)

StatusAction = Callable[[admin.ModelAdmin[Vendor], HttpRequest, QuerySet[Vendor]], None]


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


def _field_labels(names: tuple[str, ...]) -> str:
    """Labels of vendor fields; other names (e.g. "vat_number or tax_code") as they are."""
    labels = []
    for name in names:
        try:
            labels.append(legal_field_label(name))
        except FieldDoesNotExist:
            labels.append(name.replace("_", " "))
    return ", ".join(labels)


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
        "audit_trail",
        "onboarding",
        "missing_for_payout",
        "vies_status",
        "vies_checked_at",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (None, {"fields": ("display_name", "slug", "status", "public_id", "audit_trail")}),
        ("Onboarding", {"fields": ("onboarding",)}),
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
                    "tax_residence_country",
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

    @admin.display(description="Onboarding checklist")
    def onboarding(self, obj: Vendor | None) -> SafeString | str:
        """The VEN.5 checklist: only the contract blocks activation; data gaps gate payouts."""
        if obj is None or obj.pk is None:
            return "-"
        checklist = onboarding_checklist(obj)
        contract = current_contract()
        lines = []
        for item in checklist.items:
            if item.key == engine.CONTRACT:
                text = (
                    "No contract in force"
                    if contract is None
                    else f"Contract version {contract.version}: "
                    + ("accepted" if item.done else "not accepted")
                )
            else:
                name = "DAC7 data" if item.key == engine.DAC7 else "Payout data"
                text = f"{name}: " + (
                    "complete" if item.done else "missing " + _field_labels(item.missing)
                )
            if item.blocking and not item.done:
                text += " (Blocks activation)"
            lines.append((text,))
        summary = "Ready to activate" if checklist.ready else "Not ready to activate"
        return format_html(
            "<strong>{}</strong><ul>{}</ul>", summary, format_html_join("", "<li>{}</li>", lines)
        )

    @admin.display(description="Audit trail")
    def audit_trail(self, obj: Vendor) -> SafeString | str:
        if obj.pk is None:
            return "-"
        url = f"{reverse('admin:core_auditlog_changelist')}?vendor={obj.public_id}"
        return format_html('<a href="{}">Changes to this vendor</a>', url)

    def save_model(self, request: HttpRequest, obj: Vendor, form: Any, change: bool) -> None:
        save_vendor(obj, ctx=audit_context(request))

    def delete_model(self, request: HttpRequest, obj: Vendor) -> None:
        delete_vendor(obj, ctx=audit_context(request))

    def delete_queryset(self, request: HttpRequest, queryset: QuerySet[Vendor]) -> None:
        ctx = audit_context(request)
        for vendor in queryset:
            delete_vendor(vendor, ctx=ctx)


def _locked(obj: VendorContract | None) -> bool:
    return obj is not None and obj.is_accepted


class VendorContractTranslationInline(
    admin.StackedInline[VendorContractTranslation, VendorContract]
):
    model = VendorContractTranslation
    extra = 1
    fields = ("language", "status", "title", "body")

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return super().has_add_permission(request, obj) and not _locked(obj)

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return super().has_change_permission(request, obj) and not _locked(obj)

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return super().has_delete_permission(request, obj) and not _locked(obj)


class VendorContractForm(forms.ModelForm[VendorContract]):
    class Meta:
        model = VendorContract
        fields = ("valid_from", "note")

    def clean(self) -> dict[str, Any]:
        cleaned: dict[str, Any] = super().clean() or {}
        valid_from = cleaned.get("valid_from")
        if valid_from is not None:
            probe = VendorContract(
                pk=self.instance.pk, version=self.instance.version, valid_from=valid_from
            )
            try:
                check_contract_start(probe)
            except ValidationError as exc:
                self.add_error(None, exc)
        return cleaned


@admin.register(VendorContract)
class VendorContractAdmin(admin.ModelAdmin[VendorContract]):
    """Contract versions (VEN.5). A version nobody accepted yet can be edited; an accepted one
    is read-only, and versions are never deleted: new terms are a new version."""

    form = VendorContractForm
    list_display = ("version", "valid_from", "note", "acceptance_count")
    readonly_fields = ("version", "public_id", "created_at")
    fields = ("version", "valid_from", "note", "public_id", "created_at")
    inlines = (VendorContractTranslationInline,)

    @admin.display(description="Acceptances")
    def acceptance_count(self, obj: VendorContract) -> int:
        return obj.acceptances.count()

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return super().has_change_permission(request, obj) and not _locked(obj)

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def save_model(
        self, request: HttpRequest, obj: VendorContract, form: Any, change: bool
    ) -> None:
        save_contract(obj, ctx=audit_context(request))


@admin.register(ContractAcceptance)
class ContractAcceptanceAdmin(admin.ModelAdmin[ContractAcceptance]):
    """Who accepted which contract version, when and from where. Read-only: append-only rows
    written by `services.accept_contract`."""

    list_display = ("vendor", "contract", "user", "language", "accepted_at", "ip")
    list_filter = ("contract", "language")
    search_fields = ("vendor__display_name", "vendor__legal_name", "user__email")
    list_select_related = ("vendor", "contract", "user")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(LegalFieldRequirement)
class LegalFieldRequirementAdmin(admin.ModelAdmin[LegalFieldRequirement]):
    """Which legal fields vendors need, per country and legal form. Every change is audited."""

    list_display = ("field_name", "country", "region", "legal_form", "purpose", "active", "note")
    list_filter = ("purpose", "active", "country", "region", "legal_form", "field_name")
    search_fields = ("field_name", "country", "note")
    fields = ("country", "region", "legal_form", "field_name", "purpose", "active", "note")

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


def _overrides_kill_switch(override: VendorFeatureFlag | None) -> bool:
    spec = None if override is None else override.flag.spec
    return spec is not None and spec.kill_switch


@admin.register(VendorFeatureFlag)
class VendorFeatureFlagAdmin(admin.ModelAdmin[VendorFeatureFlag]):
    """Per-vendor feature flag overrides (ARC.8). Pages that can change a kill switch ask for a
    fresh second factor (CORE.4 step-up): the add page (the flag is chosen there) and the change
    and delete pages of a kill switch override. No bulk delete: it would bypass the step-up."""

    list_display = ("vendor", "flag", "kill_switch", "enabled", "note", "updated_at")
    list_filter = ("flag", "enabled")
    search_fields = ("vendor__display_name", "vendor__legal_name", "flag__name", "note")
    list_select_related = ("vendor", "flag")
    autocomplete_fields = ("vendor",)
    fields = ("vendor", "flag", "enabled", "note")
    actions = None

    @admin.display(description="Kill switch", boolean=True)
    def kill_switch(self, obj: VendorFeatureFlag) -> bool:
        return _overrides_kill_switch(obj)

    def save_model(
        self, request: HttpRequest, obj: VendorFeatureFlag, form: Any, change: bool
    ) -> None:
        save_vendor_flag(obj, ctx=audit_context(request))

    def delete_model(self, request: HttpRequest, obj: VendorFeatureFlag) -> None:
        delete_vendor_flag(obj, ctx=audit_context(request))

    def delete_queryset(self, request: HttpRequest, queryset: QuerySet[VendorFeatureFlag]) -> None:
        ctx = audit_context(request)
        for override in queryset:
            delete_vendor_flag(override, ctx=ctx)

    def add_view(
        self, request: HttpRequest, form_url: str = "", extra_context: dict[str, Any] | None = None
    ) -> HttpResponse:
        parent = super().add_view
        return step_up(lambda req: parent(req, form_url, extra_context))(request)

    def change_view(
        self,
        request: HttpRequest,
        object_id: str,
        form_url: str = "",
        extra_context: dict[str, Any] | None = None,
    ) -> HttpResponse:
        parent = super().change_view

        def view(req: HttpRequest) -> HttpResponse:
            return parent(req, object_id, form_url, extra_context)

        if _overrides_kill_switch(self.get_object(request, object_id)):
            return step_up(view)(request)
        return view(request)

    def delete_view(
        self, request: HttpRequest, object_id: str, extra_context: dict[str, Any] | None = None
    ) -> HttpResponse:
        parent = super().delete_view

        def view(req: HttpRequest) -> HttpResponse:
            return parent(req, object_id, extra_context)

        if _overrides_kill_switch(self.get_object(request, object_id)):
            return step_up(view)(request)
        return view(request)
