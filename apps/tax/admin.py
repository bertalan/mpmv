"""Staff admin for the tax configuration (TAX.1; Wagtail admin replaces it in CORE.8).

Rates and vendor tax profiles are history: staff add a new row through the audited services,
which close the current one, and can view earlier rows but never edit or delete them.
Territories and legal notes are edited through audited services; VIES answers are view-only.
"""

from typing import Any

from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db.models import Model
from django.http import HttpRequest

from apps.core.audit import audit_context

from . import services
from .models import (
    TaxRate,
    Territory,
    VatValidation,
    VendorTaxProfile,
    VendorTaxProfileTranslation,
)


class NoDeleteAdmin[M: Model](admin.ModelAdmin[M]):
    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


class HistoryAdmin[M: Model](NoDeleteAdmin[M]):
    """Versioned rows: add only (through a service), view earlier rows."""

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


class StartCheckedForm[M: Model](forms.ModelForm[M]):
    """Reports the service's start rule on `valid_from` before the service runs.

    `validate_unique` is the last step of ModelForm validation, once the instance is built and
    cleaned, so the rule sees normalized values (country codes, territory).
    """

    def check_start(self, instance: M) -> None:
        raise NotImplementedError

    def validate_unique(self) -> None:
        super().validate_unique()
        if self.instance.pk is not None or self.errors:
            return
        try:
            self.check_start(self.instance)
        except ValidationError as exc:
            self.add_error("valid_from", exc.message_dict["valid_from"])


class TaxRateForm(StartCheckedForm[TaxRate]):
    class Meta:
        model = TaxRate
        fields = ("country", "territory", "category", "rate", "source", "valid_from")

    def check_start(self, instance: TaxRate) -> None:
        services.check_rate_start(instance)


@admin.register(TaxRate)
class TaxRateAdmin(HistoryAdmin[TaxRate]):
    form = TaxRateForm
    list_display = ("country", "territory", "category", "rate", "source", "valid_from", "valid_to")
    list_filter = ("category", "source", "country")
    list_select_related = ("territory",)
    readonly_fields = ("public_id", "valid_to", "created_at")

    def save_model(self, request: HttpRequest, obj: TaxRate, form: Any, change: bool) -> None:
        services.configure_tax_rate(obj, ctx=audit_context(request))


@admin.register(Territory)
class TerritoryAdmin(NoDeleteAdmin[Territory]):
    """Rates point at territories (PROTECT), so they are changed, never deleted."""

    list_display = ("code", "name", "country", "vat_status", "postcode_patterns")
    list_filter = ("vat_status", "country")
    search_fields = ("code", "name")
    readonly_fields = ("public_id", "created_at", "updated_at")

    def save_model(self, request: HttpRequest, obj: Territory, form: Any, change: bool) -> None:
        services.save_territory(obj, ctx=audit_context(request))


class VendorTaxProfileForm(StartCheckedForm[VendorTaxProfile]):
    class Meta:
        model = VendorTaxProfile
        fields = (
            "vendor",
            "regime",
            "exempt_reason",
            "oss_registered_since",
            "requested_by",
            "valid_from",
        )

    def check_start(self, instance: VendorTaxProfile) -> None:
        services.check_profile_start(instance)


@admin.register(VendorTaxProfile)
class VendorTaxProfileAdmin(HistoryAdmin[VendorTaxProfile]):
    """Adding a profile here approves it in the staff member's name (TAX.2 adds requests)."""

    form = VendorTaxProfileForm
    list_display = ("vendor", "regime", "exempt_reason", "valid_from", "valid_to", "approved_by")
    list_filter = ("regime", "exempt_reason")
    search_fields = ("vendor__display_name", "vendor__legal_name")
    list_select_related = ("vendor", "approved_by")
    raw_id_fields = ("vendor", "requested_by")
    readonly_fields = ("public_id", "valid_to", "approved_by", "created_at")

    def save_model(
        self, request: HttpRequest, obj: VendorTaxProfile, form: Any, change: bool
    ) -> None:
        obj.approved_by = request.user  # type: ignore[assignment]
        services.configure_vendor_tax_profile(obj, ctx=audit_context(request))


@admin.register(VendorTaxProfileTranslation)
class VendorTaxProfileTranslationAdmin(NoDeleteAdmin[VendorTaxProfileTranslation]):
    """Legal notes per language; the profile version they belong to never changes."""

    list_display = ("profile", "language", "status")
    list_filter = ("language", "status")
    search_fields = ("profile__vendor__display_name", "legal_note")
    list_select_related = ("profile__vendor",)
    raw_id_fields = ("profile",)
    readonly_fields = ("public_id", "source_hash", "created_at", "updated_at")

    def save_model(
        self, request: HttpRequest, obj: VendorTaxProfileTranslation, form: Any, change: bool
    ) -> None:
        services.save_legal_note(obj, ctx=audit_context(request))


@admin.register(VatValidation)
class VatValidationAdmin(HistoryAdmin[VatValidation]):
    """Answers are recorded by the VIES client (TAX.5); staff only look at them."""

    list_display = ("checked_at", "country", "vat_number", "result", "name")
    list_filter = ("result", "country")
    search_fields = ("vat_number", "name")
    readonly_fields = (
        "public_id",
        "country",
        "vat_number",
        "result",
        "name",
        "address",
        "request_id",
        "checked_at",
        "created_at",
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False
