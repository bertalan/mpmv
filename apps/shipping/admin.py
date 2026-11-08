"""Staff admin for the shipping configuration (Wagtail admin and the SHP.6 editor replace it).

Rates are prices, so every create, change and delete made here, inlines included, goes through
the audited services in `apps.shipping.services` (dry-run capable, AuditLog with before and
after); the admin never saves these rows itself. Configuration versions are history: view only.
"""

from typing import Any

from django import forms
from django.contrib import admin
from django.db import models
from django.db.models import QuerySet
from django.forms.models import BaseInlineFormSet
from django.http import HttpRequest

from apps.core.audit import audit_context

from . import engine
from .models import (
    Carrier,
    CarrierTranslation,
    CountryGroup,
    CountryGroupTranslation,
    PickupOption,
    PickupOptionTranslation,
    ShippingConfigVersion,
    ShippingMethod,
    ShippingMethodTranslation,
    ShippingZone,
    SpecialArea,
    SpecialAreaTreatment,
    WeightBand,
    zones_using_groups,
)
from .services import CONFIG_MODELS, delete_config, save_config


class AuditedAdmin[M: models.Model](admin.ModelAdmin[M]):
    """Writes every row it, or one of its audited inlines, creates, changes or deletes through
    the shipping services. The admin runs each save in a transaction, so a failed save audits
    nothing."""

    def save_model(self, request: HttpRequest, obj: M, form: Any, change: bool) -> None:
        save_config(obj, ctx=audit_context(request))  # type: ignore[arg-type]

    def delete_model(self, request: HttpRequest, obj: M) -> None:
        delete_config([obj], ctx=audit_context(request))  # type: ignore[list-item]

    def delete_queryset(self, request: HttpRequest, queryset: QuerySet[M]) -> None:
        delete_config(queryset, ctx=audit_context(request))  # type: ignore[arg-type]

    def save_formset(self, request: HttpRequest, form: Any, formset: Any, change: bool) -> None:
        if formset.model not in CONFIG_MODELS:  # translations: not audited
            super().save_formset(request, form, formset, change)
            return
        ctx = audit_context(request)
        rows = formset.save(commit=False)  # collects new, changed and deleted rows, writes none
        delete_config(formset.deleted_objects, ctx=ctx)
        for obj in rows:
            save_config(obj, ctx=ctx)
        formset.save_m2m()


class CarrierTranslationInline(admin.TabularInline[CarrierTranslation, Carrier]):
    model = CarrierTranslation
    extra = 1
    fields = ("language", "status", "label")


@admin.register(Carrier)
class CarrierAdmin(AuditedAdmin[Carrier]):
    list_display = ("code", "is_active", "supports_api")
    list_filter = ("is_active",)
    search_fields = ("code", "translations__label")
    readonly_fields = ("public_id", "created_at", "updated_at")
    inlines = (CarrierTranslationInline,)


class CountryGroupTranslationInline(admin.TabularInline[CountryGroupTranslation, CountryGroup]):
    model = CountryGroupTranslation
    extra = 1
    fields = ("language", "status", "name")


@admin.register(CountryGroup)
class CountryGroupAdmin(AuditedAdmin[CountryGroup]):
    list_display = ("code", "countries")
    search_fields = ("code", "translations__name")
    readonly_fields = ("public_id", "created_at", "updated_at")
    inlines = (CountryGroupTranslationInline,)

    def get_deleted_objects(
        self, objs: Any, request: HttpRequest
    ) -> tuple[list[Any], dict[Any, Any], set[Any], list[Any]]:
        """Zones name groups by code, which Django's collector cannot see: list the zones that
        use them as protected, so the delete page refuses instead of failing."""
        deleted, counts, perms_needed, protected = super().get_deleted_objects(objs, request)
        codes = [group.code for group in objs]
        protected = [*protected, *(str(zone) for zone in zones_using_groups(codes))]
        return deleted, counts, perms_needed, protected


@admin.register(ShippingZone)
class ShippingZoneAdmin(AuditedAdmin[ShippingZone]):
    list_display = ("code", "name", "vendor", "priority", "countries", "country_groups")
    list_filter = (("vendor", admin.EmptyFieldListFilter),)
    search_fields = ("code", "name", "vendor__display_name")
    list_select_related = ("vendor",)
    readonly_fields = ("public_id", "created_at", "updated_at")


class WeightBandFormSet(BaseInlineFormSet[WeightBand, ShippingMethod, Any]):
    """Refuses overlapping bands within one submission; the database refuses the rest."""

    def clean(self) -> None:
        super().clean()
        bands = [
            (form.cleaned_data["from_kg"], form.cleaned_data.get("to_kg"))
            for form in self.forms
            if form.cleaned_data
            and not form.cleaned_data.get("DELETE")
            and form.cleaned_data.get("from_kg") is not None
        ]
        pairs = engine.overlapping_bands(bands)
        if pairs:
            described = "; ".join(f"{_kg(bands[i])} and {_kg(bands[j])}" for i, j in pairs)
            raise forms.ValidationError(f"Weight bands overlap: {described}.")


def _kg(band: engine.Band) -> str:
    upper = "unlimited" if band[1] is None else f"{band[1]}"
    return f"{band[0]}-{upper} kg"


class ShippingMethodTranslationInline(
    admin.TabularInline[ShippingMethodTranslation, ShippingMethod]
):
    model = ShippingMethodTranslation
    extra = 0
    fields = ("language", "status", "name")


class WeightBandInline(admin.TabularInline[WeightBand, ShippingMethod]):
    model = WeightBand
    formset = WeightBandFormSet
    extra = 1
    fields = ("from_kg", "to_kg", "cost_net", "surcharge_net")


class SpecialAreaTreatmentInline(admin.TabularInline[SpecialAreaTreatment, ShippingMethod]):
    """What the method does in each special area; an area it does not list gets the default,
    an indicative cost subject to quote (SHP.14)."""

    model = SpecialAreaTreatment
    extra = 0
    fields = ("area", "mode", "surcharge_type", "surcharge", "extra_days")
    autocomplete_fields = ("area",)


@admin.register(SpecialArea)
class SpecialAreaAdmin(AuditedAdmin[SpecialArea]):
    list_display = ("code", "name", "kind", "countries", "territory_code")
    list_filter = ("kind",)
    search_fields = ("code", "name", "territory_code")
    readonly_fields = ("public_id", "created_at", "updated_at")


@admin.register(ShippingMethod)
class ShippingMethodAdmin(AuditedAdmin[ShippingMethod]):
    list_display = (
        "vendor",
        "zone",
        "key",
        "carrier",
        "service_level",
        "is_default",
        "tax_treatment",
        "over_max_weight",
    )
    list_filter = ("carrier", "service_level", "tax_treatment", "is_default", "zone")
    search_fields = ("vendor__display_name", "zone__code", "key")
    list_select_related = ("vendor", "zone", "carrier")
    readonly_fields = ("public_id", "created_at", "updated_at")
    inlines = (ShippingMethodTranslationInline, WeightBandInline, SpecialAreaTreatmentInline)


class PickupOptionTranslationInline(admin.TabularInline[PickupOptionTranslation, PickupOption]):
    model = PickupOptionTranslation
    extra = 1
    fields = ("language", "status", "label")


@admin.register(PickupOption)
class PickupOptionAdmin(AuditedAdmin[PickupOption]):
    list_display = ("vendor", "key", "cost_net")
    search_fields = ("vendor__display_name", "key")
    list_select_related = ("vendor",)
    readonly_fields = ("public_id", "created_at", "updated_at")
    inlines = (PickupOptionTranslationInline,)


@admin.register(ShippingConfigVersion)
class ShippingConfigVersionAdmin(admin.ModelAdmin[ShippingConfigVersion]):
    """Versions are written by the editor (SHP.6); staff only look at them here."""

    list_display = ("created_at", "created_by", "note")
    fields = ("public_id", "created_at", "created_by", "note", "snapshot")
    readonly_fields = fields

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False
