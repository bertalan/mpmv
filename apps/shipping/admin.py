"""Staff admin for the shipping configuration (Wagtail admin and the SHP.6 editor replace it).

Rates are prices, so every create, change and delete made here, inlines included, writes an
AuditLog entry with before and after. Configuration versions are history: view only.
"""

from typing import Any

from django import forms
from django.contrib import admin
from django.db import models
from django.db.models import QuerySet
from django.forms.models import BaseInlineFormSet, model_to_dict
from django.http import HttpRequest

from apps.core.audit import audit_context, record
from apps.core.changeset import diff
from apps.core.models import AuditLog

from . import engine
from .models import (
    Carrier,
    CarrierTranslation,
    PickupOption,
    PickupOptionTranslation,
    ShippingConfigVersion,
    ShippingZone,
    VendorZoneRate,
    WeightBand,
)


def snapshot(obj: models.Model) -> dict[str, Any]:
    """The editable fields of a row, as stored in the database."""
    return model_to_dict(obj)


def stored(obj: models.Model) -> dict[str, Any] | None:
    """The row as the database has it now (before an in-memory edit is saved)."""
    if obj.pk is None:
        return None
    row = type(obj)._default_manager.filter(pk=obj.pk).first()
    return None if row is None else snapshot(row)


def audit(
    request: HttpRequest, obj: models.Model, op: str, before: Any, after: Any
) -> AuditLog | None:
    if op == "updated" and not diff(before or {}, after or {}):
        return None  # saved without changes
    meta = obj._meta
    return record(
        audit_context(request),
        f"{meta.label_lower}.{op}",
        meta.label_lower,
        str(getattr(obj, "public_id", obj.pk)),
        before,
        after,
    )


class AuditedAdmin[M: models.Model](admin.ModelAdmin[M]):
    """Writes an AuditLog entry for every row it, or one of its inlines, creates, changes or
    deletes. The admin runs each save in a transaction, so a failed save audits nothing."""

    def save_model(self, request: HttpRequest, obj: M, form: Any, change: bool) -> None:
        before = stored(obj) if change else None
        super().save_model(request, obj, form, change)
        audit(request, obj, "updated" if change else "created", before, snapshot(obj))

    def delete_model(self, request: HttpRequest, obj: M) -> None:
        before = snapshot(obj)
        super().delete_model(request, obj)
        audit(request, obj, "deleted", before, None)

    def delete_queryset(self, request: HttpRequest, queryset: QuerySet[M]) -> None:
        rows = [(obj, snapshot(obj)) for obj in queryset]
        super().delete_queryset(request, queryset)
        for obj, before in rows:
            audit(request, obj, "deleted", before, None)

    def save_formset(self, request: HttpRequest, form: Any, formset: Any, change: bool) -> None:
        before = {id(f.instance): stored(f.instance) for f in formset.initial_forms}
        super().save_formset(request, form, formset, change)
        for obj in formset.new_objects:
            audit(request, obj, "created", None, snapshot(obj))
        for obj, _fields in formset.changed_objects:
            audit(request, obj, "updated", before.get(id(obj)), snapshot(obj))
        for obj in formset.deleted_objects:
            audit(request, obj, "deleted", before.get(id(obj)), None)


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


@admin.register(ShippingZone)
class ShippingZoneAdmin(AuditedAdmin[ShippingZone]):
    list_display = ("code", "name", "priority", "countries", "postcode_patterns")
    search_fields = ("code", "name")
    readonly_fields = ("public_id", "created_at", "updated_at")


class WeightBandFormSet(BaseInlineFormSet[WeightBand, VendorZoneRate, Any]):
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


class WeightBandInline(admin.TabularInline[WeightBand, VendorZoneRate]):
    model = WeightBand
    formset = WeightBandFormSet
    extra = 1
    fields = ("from_kg", "to_kg", "cost_net", "surcharge_net")


@admin.register(VendorZoneRate)
class VendorZoneRateAdmin(AuditedAdmin[VendorZoneRate]):
    list_display = ("vendor", "zone", "carrier", "over_max_weight")
    list_filter = ("carrier", "zone")
    search_fields = ("vendor__display_name", "zone__code")
    list_select_related = ("vendor", "zone", "carrier")
    readonly_fields = ("public_id", "created_at", "updated_at")
    inlines = (WeightBandInline,)


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
