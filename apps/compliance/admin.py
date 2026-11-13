"""Staff admin for GPSR data and the article 22 safety cases (CMP.3).

Every write goes through the audited compliance services, never a plain model save. GPSR data
is never deleted here (empty a block to use the vendor defaults again), and safety cases are
never deleted or edited after the fact: staff open one, then close it with what was done. The
country -> warnings languages map is edited here too, never deleted (a country leaves the check
only through a code change).
"""

import datetime as dt
from typing import Any

from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _

from apps.core.audit import audit_context

from . import engine, services
from .models import (
    CountryWarningLanguages,
    GpsrInfo,
    GpsrSafetyText,
    SafetyCase,
    VendorGpsrDefaults,
)


class _GpsrPartyForm(forms.ModelForm[Any]):
    """Contacts must be an email or a web address (the service checks it again)."""

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        for name in engine.CONTACT_FIELDS:
            message = engine.contact_error(cleaned.get(name) or "")
            if message is not None:
                self.add_error(name, message)
        return cleaned


PARTY_FIELDSETS: Any = (
    ("Manufacturer", {"fields": engine.MANUFACTURER_FIELDS}),
    (
        "Responsible person in the EU (mandatory when the manufacturer is outside the EU)",
        {"fields": engine.EU_RESPONSIBLE_FIELDS},
    ),
)


class _NoDelete:
    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(VendorGpsrDefaults)
class VendorGpsrDefaultsAdmin(_NoDelete, admin.ModelAdmin[VendorGpsrDefaults]):
    form = _GpsrPartyForm
    list_display = ("vendor", "manufacturer_name", "manufacturer_country", "eu_responsible_name")
    search_fields = ("vendor__display_name", "manufacturer_name")
    raw_id_fields = ("vendor",)
    fieldsets = ((None, {"fields": ("vendor",)}), *PARTY_FIELDSETS)

    def get_readonly_fields(self, request: HttpRequest, obj: Any = None) -> tuple[str, ...]:
        return ("vendor",) if obj is not None else ()

    def save_model(self, request: HttpRequest, obj: Any, form: Any, change: bool) -> None:
        data = {name: getattr(obj, name) for name in services.VENDOR_DEFAULTS_FIELDS}
        services.set_vendor_gpsr_defaults(obj.vendor, data, ctx=audit_context(request))
        saved = VendorGpsrDefaults.objects.get(vendor=obj.vendor)
        obj.pk, obj.public_id = saved.pk, saved.public_id


@admin.register(GpsrInfo)
class GpsrInfoAdmin(_NoDelete, admin.ModelAdmin[GpsrInfo]):
    form = _GpsrPartyForm
    list_display = ("product", "manufacturer_name", "model_number", "batch")
    search_fields = ("product__sku", "manufacturer_name", "model_number")
    list_select_related = ("product__vendor",)
    raw_id_fields = ("product",)
    fieldsets = (
        (None, {"fields": ("product", "model_number", "batch")}),
        *PARTY_FIELDSETS,
    )

    def get_readonly_fields(self, request: HttpRequest, obj: Any = None) -> tuple[str, ...]:
        return ("product",) if obj is not None else ()

    def save_model(self, request: HttpRequest, obj: Any, form: Any, change: bool) -> None:
        data = {name: getattr(obj, name) for name in services.PRODUCT_GPSR_FIELDS}
        services.set_product_gpsr(obj.product, data, ctx=audit_context(request))
        saved = GpsrInfo.objects.get(product=obj.product)
        obj.pk, obj.public_id = saved.pk, saved.public_id


@admin.register(GpsrSafetyText)
class GpsrSafetyTextAdmin(_NoDelete, admin.ModelAdmin[GpsrSafetyText]):
    list_display = ("product", "language")
    list_filter = ("language",)
    search_fields = ("product__sku",)
    raw_id_fields = ("product",)
    fields = ("product", "language", "warnings", "safety_information")

    def get_fields(self, request: HttpRequest, obj: Any = None) -> tuple[str, ...]:
        return (*self.fields, "sale_blocked_into") if obj is not None else self.fields

    def get_readonly_fields(self, request: HttpRequest, obj: Any = None) -> tuple[str, ...]:
        return ("product", "language", "sale_blocked_into") if obj is not None else ()

    @admin.display(description="Sale blocked into")
    def sale_blocked_into(self, obj: GpsrSafetyText) -> str:
        """Countries the product is not sold into until its warnings exist in their language
        (owner decision 2026-11-09). The vendor's view is the Manager shop space (CORE.17)."""
        gaps = services.warnings_language_gaps(obj.product)
        if not gaps:
            return "None: the warnings cover every destination country's languages."
        return "; ".join(
            f"{gap.country} (needs {' or '.join(gap.languages)}"
            + ("" if gap.offered else ", not offered by the platform yet")
            + ")"
            for gap in gaps
        )

    def save_model(self, request: HttpRequest, obj: Any, form: Any, change: bool) -> None:
        data = {name: getattr(obj, name) for name in services.SAFETY_TEXT_FIELDS}
        services.set_product_safety_text(
            obj.product, obj.language, data, ctx=audit_context(request)
        )
        saved = GpsrSafetyText.objects.get(product=obj.product, language=obj.language)
        obj.pk, obj.public_id = saved.pk, saved.public_id


class SafetyCaseOpenForm(forms.ModelForm[SafetyCase]):
    class Meta:
        model = SafetyCase
        fields = ("vendor", "product", "kind", "received_at", "reference", "authority", "summary")

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        product, vendor = cleaned.get("product"), cleaned.get("vendor")
        if product is not None and vendor is not None and product.vendor_id != vendor.pk:
            self.add_error("product", "The product belongs to another vendor.")
        return cleaned


class SafetyCaseCloseForm(forms.ModelForm[SafetyCase]):
    close = forms.BooleanField(required=False, help_text="Close the case with this resolution.")

    class Meta:
        model = SafetyCase
        fields = ("resolution",)

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        if cleaned.get("close") and not (cleaned.get("resolution") or "").strip():
            raise ValidationError({"resolution": "Say what was done."})
        return cleaned


OPEN_FIELDS = ("vendor", "product", "kind", "received_at", "reference", "authority", "summary")
CASE_READONLY = (
    "vendor",
    "product",
    "product_sku",
    "kind",
    "reference",
    "authority",
    "summary",
    "received_at",
    "due_on",
    "deadline",
    "status",
    "closed_at",
    "overdue_alerted_at",
)


@admin.register(SafetyCase)
class SafetyCaseAdmin(_NoDelete, admin.ModelAdmin[SafetyCase]):
    """GPSR article 22: authority orders (2 working days) and safety notices (3)."""

    list_display = ("__str__", "vendor", "kind", "received_at", "due_on", "deadline", "status")
    list_filter = ("status", "kind")
    search_fields = ("reference", "product_sku", "vendor__display_name")
    list_select_related = ("vendor",)
    raw_id_fields = ("vendor", "product")
    date_hierarchy = "received_at"

    @admin.display(description="Deadline")
    def deadline(self, obj: SafetyCase) -> str:
        if obj.status == SafetyCase.Status.CLOSED:
            return "Closed"
        deadline = services.safety_case_deadline(obj)
        if deadline.overdue:
            return f"Overdue since {(deadline.due_on + dt.timedelta(days=1)).isoformat()}"
        return f"{deadline.working_days_left} working day(s) left"

    def get_form(
        self, request: HttpRequest, obj: Any = None, change: bool = False, **kwargs: Any
    ) -> Any:
        kwargs["form"] = SafetyCaseOpenForm if obj is None else SafetyCaseCloseForm
        return super().get_form(request, obj, change, **kwargs)

    def get_fields(self, request: HttpRequest, obj: Any = None) -> Any:
        if obj is None:
            return OPEN_FIELDS
        if obj.status == SafetyCase.Status.CLOSED:
            return (*CASE_READONLY, "resolution")
        return (*CASE_READONLY, "resolution", "close")

    def get_readonly_fields(self, request: HttpRequest, obj: Any = None) -> tuple[str, ...]:
        if obj is None:
            return ()
        if obj.status == SafetyCase.Status.CLOSED:
            return (*CASE_READONLY, "resolution")
        return CASE_READONLY

    def save_model(self, request: HttpRequest, obj: Any, form: Any, change: bool) -> None:
        ctx = audit_context(request)
        if not change:
            changes = services.open_safety_case(
                vendor=obj.vendor,
                product=obj.product,
                kind=obj.kind,
                received_at=obj.received_at,
                reference=obj.reference,
                authority=obj.authority,
                summary=obj.summary,
                ctx=ctx,
            )
            saved = SafetyCase.objects.get(public_id=changes.changes[0].key)
            obj.pk, obj.public_id, obj.due_on = saved.pk, saved.public_id, saved.due_on
        elif form.cleaned_data.get("close"):
            services.close_safety_case(obj, resolution=obj.resolution, ctx=ctx)


class CountryWarningLanguagesForm(forms.ModelForm[CountryWarningLanguages]):
    """Languages as one comma-separated field; the service validates them again."""

    languages = forms.CharField(
        help_text=_(
            "The languages in which this country accepts product warnings, as two-letter codes "
            "such as 'fr, nl, de', separated by commas. Warnings in any one of them are "
            "enough to sell the product into the country."
        )
    )

    class Meta:
        model = CountryWarningLanguages
        fields = ("country",)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if self.instance.pk is not None and not self.is_bound:
            self.initial["languages"] = ", ".join(self.instance.languages)

    def clean_languages(self) -> list[str]:
        try:
            return services.clean_languages(
                part for part in str(self.cleaned_data["languages"]).split(",") if part.strip()
            )
        except ValidationError as exc:
            raise ValidationError(exc.messages) from exc


@admin.register(CountryWarningLanguages)
class CountryWarningLanguagesAdmin(_NoDelete, admin.ModelAdmin[CountryWarningLanguages]):
    """The languages each destination accepts for warnings (CMP.3, owner clarification)."""

    form = CountryWarningLanguagesForm
    list_display = ("country", "languages_list", "updated_at")
    search_fields = ("country",)

    @admin.display(description="Accepted languages")
    def languages_list(self, obj: CountryWarningLanguages) -> str:
        return ", ".join(obj.languages)

    def get_readonly_fields(self, request: HttpRequest, obj: Any = None) -> tuple[str, ...]:
        return ("country",) if obj is not None else ()

    def save_model(self, request: HttpRequest, obj: Any, form: Any, change: bool) -> None:
        services.set_country_warning_languages(
            obj.country, form.cleaned_data["languages"], ctx=audit_context(request)
        )
        saved = CountryWarningLanguages.objects.get(country=obj.country)
        obj.pk, obj.public_id = saved.pk, saved.public_id
